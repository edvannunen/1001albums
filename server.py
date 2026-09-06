"""
1001 Albums backend — FastAPI app. This is the one process meant to run in
production (Coolify) and locally: it serves the static frontend, the live
data route, and the admin page, so local testing matches production
exactly instead of the old two-piece "static file server + separate API"
setup.

Deployed at https://bier-en-brood.nl/1001albums — a literal subpath, not a
subdomain, shared with other projects on the same domain (De Sprong is the
other one, at /de-sprong, which hit this exact same class of bug).
Confirmed empirically across two separate failures that Traefik/Coolify
STRIPS the "/1001albums" prefix before forwarding to this container:

  1. Routes registered under a "/1001albums" prefix 404'd — fixed by
     moving every route to plain root paths ("/", "/admin/", ...). The app
     never sees the prefix at all, so its own routing must not include it.
  2. That alone wasn't sufficient: relative asset paths (css/x.css, not
     /css/x.css) resolve against the BROWSER's current address-bar URL, not
     against whatever Traefik forwarded internally. Visiting the bare
     "https://bier-en-brood.nl/1001albums" (no trailing slash) correctly
     served index.html (Traefik strips the prefix regardless), but every
     relative asset then resolved one level too high (against the domain
     root, dropping "/1001albums" entirely) — broken CSS, no data. A
     same-origin redirect can't fix this: by the time our app sees the
     request, the prefix is already gone, so there's no way to reconstruct
     the correct external Location to redirect to.

Fixed with an HTML <base href="..."> tag, which overrides relative-URL
resolution for the whole page unconditionally, regardless of what the
address bar shows or how the page was reached (bookmark, bare URL,
mid-navigation, whatever). BASE_PATH below is empty locally (no proxy, no
prefix to correct for) and "/1001albums" in production (set as a Coolify
env var) — <base href="{BASE_PATH}/"> then degrades to <base href="/">
locally, a harmless no-op-equivalent for a root-served app.

  - GET / and /css, /js, /img — the existing static frontend (index.html +
    js/*.js + css/styles.css + img assets).
  - GET /albums_enriched.json — live export straight from the DB
    (db.export_from_db), same shape the pipeline has always produced.
    js/data.js's fetch("albums_enriched.json") is a *relative* URL, so it
    resolves against the injected <base> the same way HTML attributes do.
  - /admin/ — a single authenticated page: paste a new Medium post URL, it
    runs sync_posts() (from enrich_1001_albums.py) for just that one URL —
    scrape, merge into the DB, Spotify/MusicBrainz enrichment only for
    genuinely new albums. Its form action is "admin/add-post" (relative to
    the shared site-root <base>, not to the admin page's own directory —
    <base> makes ALL relative resolution in the document relative to the
    same fixed value, not to each element's own location).
  - /admin/add-post-relay — same end result as /admin/add-post, but takes
    an already-fetched Apollo state instead of fetching Medium itself.
    Exists because Medium/Cloudflare intermittently challenges requests
    from this server's own (Hetzner datacenter) IP — confirmed 2026-08-18,
    not a fixed block, Cloudflare's bot-fight scoring just flags
    datacenter ASNs on and off — which a plain server-side `requests.get`
    can never pass (it's a JS challenge, not a header check). When
    /admin/add-post's direct fetch fails this way, run
    `python relay_add_post.py <url>` locally instead — it fetches from a
    normal residential IP (which Cloudflare doesn't challenge) and POSTs
    the result here.

Run locally: uvicorn server:app --reload --port 8000, then browse
http://localhost:8000/ — BASE_PATH is unset/empty locally, so <base
href="/"> is a no-op and everything resolves exactly as it did before this
fix was needed.
Auth: HTTP Basic, single shared username/password from .env
(ADMIN_USERNAME/ADMIN_PASSWORD) — single-user tool, no per-user accounts.
"""

import html
import json
import os
import queue
import re
import secrets
import threading
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from db import export_from_db, get_connection
from enrich_1001_albums import sync_posts, sync_prefetched_post

load_dotenv()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
BASE_PATH = os.environ.get("BASE_PATH", "")  # e.g. "/1001albums" in Coolify

app = FastAPI()
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    # compare_digest avoids leaking match-length via response timing
    valid_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    valid_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/albums_enriched.json")
def albums_json():
    return JSONResponse(export_from_db())


# ---------------------------------------------------------------------------
# Per-album Open Graph tags for social share previews
# ---------------------------------------------------------------------------
# Sharing a deep link like "/?album=255-the-nitty-gritty-dirt-band-..." should
# show that album's own art/title/blurb in Bluesky/Twitter/etc.'s link
# preview card, not the generic dashboard one baked into index.html. Crawlers
# don't run JS, so the client-side deep-link handling in app.js (which opens
# the right modal on page load) is invisible to them — the <meta> tags in the
# HTML response itself have to already be correct. _album_slug/_find_album_
# by_param mirror albumSlug()/findAlbumByParam() in js/data.js exactly (same
# slug format, same "number prefix" fallback for hand-typed/legacy URLs) —
# keep the two in sync if either changes.

DEFAULT_OG_IMAGE = "https://bier-en-brood.nl/1001albums/img/social_media.png"


def _slugify(s: str) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # strip combining marks
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _album_slug(a: dict) -> str:
    return f"{a['number']}-{_slugify(a['artist'] + ' ' + a['album'])}"


def _find_album_by_param(param: str, albums: list) -> dict | None:
    if not param:
        return None
    exact = next((a for a in albums if _album_slug(a) == param), None)
    if exact:
        return exact
    number = param.split("-")[0]
    return next((a for a in albums if a["number"] == number), None)


def _album_og_tags(a: dict, lang: str) -> dict:
    text = (a.get("text_en") if lang == "en" and a.get("text_en") else a.get("text")) or ""
    text = " ".join(text.split())  # collapse newlines/repeated whitespace
    description = text if len(text) <= 200 else text[:197].rstrip() + "..."
    image = (
        (a.get("spotify") or {}).get("cover_art_url")
        or (a.get("musicbrainz") or {}).get("cover_art_archive_url")
        or DEFAULT_OG_IMAGE
    )
    title = f"{a['number']} {a['artist']} — {a['album']} ({a['year']})"
    return {"title": title, "description": description, "image": image}


def _inject_album_meta(html_text: str, tags: dict, url: str) -> str:
    title = html.escape(tags["title"], quote=True)
    html_text = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", html_text, count=1)
    for prop, value in (
        ("og:title", tags["title"]),
        ("og:description", tags["description"]),
        ("og:image", tags["image"]),
        ("og:url", url),
    ):
        html_text = re.sub(
            rf'<meta property="{prop}" content="[^"]*">',
            f'<meta property="{prop}" content="{html.escape(value, quote=True)}">',
            html_text, count=1,
        )
    return html_text


ADMIN_PAGE = """<!doctype html>
<html>
<head><title>1001 Albums admin</title><base href="{base}/"></head>
<body style="font-family: sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem;">
  <h1>Add a Medium post</h1>
  <p><a href="admin/reddit-export">Reddit export tool →</a></p>
  <form method="post" action="admin/add-post">
    <input name="url" type="url" placeholder="https://edvannunen.medium.com/..." value="{url}"
           style="width:100%; padding:0.5rem; box-sizing:border-box;" required>
    <button type="submit" id="add-btn" style="margin-top:0.75rem; padding:0.5rem 1rem;">Scrape &amp; add</button>{relay_button}
  </form>
  <pre id="admin-log" style="display:none; background:#f4f4f4; padding:0.75rem;
       margin-top:0.75rem; max-height:20rem; overflow:auto; white-space:pre-wrap;
       font-size:0.85rem;"></pre>
  {result}
  <script>
    // Live progress for both "Scrape & add" and "Scrape locally & push to
    // production": intercepts the click and streams Server-Sent Events from
    // the matching *-stream endpoint instead of the plain form POST (whose
    // formaction stays wired up as a no-JS fallback for each button). A
    // genuinely new post can take 60-90s+ (Spotify + rate-limited
    // MusicBrainz + translation) and a blank page for that long reads as
    // "did this hang?" — see CLAUDE.md for the incident this fixed.
    (function() {{
      var log = document.getElementById('admin-log');
      function wire(btn, streamPath, formatDone) {{
        if (!btn || !window.EventSource) return;
        btn.addEventListener('click', function(ev) {{
          var input = document.querySelector('input[name=url]');
          var url = input.value;
          if (!url) return;
          ev.preventDefault();
          log.style.display = 'block';
          log.textContent = '';
          btn.disabled = true;
          var es = new EventSource(streamPath + '?url=' + encodeURIComponent(url));
          es.onmessage = function(e) {{
            log.textContent += e.data + '\\n';
            log.scrollTop = log.scrollHeight;
          }};
          es.addEventListener('done', function(e) {{
            var stats = JSON.parse(e.data);
            log.textContent += formatDone(stats, url) + '\\n';
            es.close();
            btn.disabled = false;
          }});
          es.addEventListener('error', function(e) {{
            log.textContent += (e.data ? 'Failed: ' + e.data : 'Connection lost.') + '\\n';
            es.close();
            btn.disabled = false;
          }});
        }});
      }}
      wire(document.getElementById('add-btn'), 'admin/add-post-stream', function(stats, url) {{
        return stats.failed_urls && stats.failed_urls.length
          ? 'Failed to scrape ' + url + ' — check the URL and try again.'
          : 'Done — ' + stats.new + ' new album(s) added, '
            + stats.updated + ' already existed and had their text/media refreshed.';
      }});
      wire(document.getElementById('push-btn'), 'admin/relay-to-prod-stream', function(stats, url) {{
        return stats.failed_urls && stats.failed_urls.length
          ? 'Production failed to process ' + url + ' — check the URL and try again.'
          : 'Pushed to production — ' + stats.new + ' new album(s) added, '
            + stats.updated + ' already existed and had their text/media refreshed.';
      }});
    }})();
  </script>
</body>
</html>"""

# Only meaningful when running locally: this is the process that has a
# normal (non-datacenter) IP Cloudflare doesn't challenge, so it's the one
# that can actually do the relay fetch. On production BASE_PATH is set
# ("/1001albums"), so this button is simply omitted from the rendered page
# — see the "why this exists" note on /admin/relay-to-prod below.
RELAY_BUTTON = (
    '<button type="submit" formaction="admin/relay-to-prod" id="push-btn" '
    'style="margin-top:0.75rem; margin-left:0.5rem; padding:0.5rem 1rem;">'
    "Scrape locally &amp; push to production</button>"
)


def _admin_page(result: str, url: str = "") -> str:
    return ADMIN_PAGE.format(
        base=BASE_PATH, result=result, url=html.escape(url),
        relay_button=RELAY_BUTTON if not BASE_PATH else ""
    )


@app.get("/admin/", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    return _admin_page("")


@app.get("/admin/reddit-export", response_class=HTMLResponse)
def reddit_export_page(_: str = Depends(require_admin)):
    """Standalone tool: paste a catalog number, get a paste-ready Reddit
    post (header + review text + one title/link/thumbnail block per
    YouTube clip) plus a "See also" link back to the real site. Gated
    behind admin auth like the rest of /admin — it's a personal authoring
    tool, not part of the public site. Same <base> injection as index()
    since it fetches albums_enriched.json and imports js/data.js relatively.
    """
    page = Path("reddit_export.html").read_text(encoding="utf-8")
    page = page.replace("<head>", f'<head>\n<base href="{BASE_PATH}/">', 1)
    return HTMLResponse(page)


@app.post("/admin/add-post", response_class=HTMLResponse)
def add_post(url: str = Form(...), _: str = Depends(require_admin)):
    conn = get_connection()
    try:
        stats = sync_posts([url], conn)
    finally:
        conn.close()

    if stats["failed_urls"]:
        result = f"<p style='color:#b00'>Failed to scrape {url} — check the URL and try again.</p>"
    else:
        result = (
            f"<p>Done — {stats['new']} new album(s) added"
            f"{' (Spotify/MusicBrainz enrichment ran for those)' if stats['new'] else ''}, "
            f"{stats['updated']} already existed and had their text/media refreshed.</p>"
        )
    return _admin_page(result, url)


@app.get("/admin/add-post-stream")
def add_post_stream(url: str, _: str = Depends(require_admin)):
    """SSE version of /admin/add-post — same live-progress motivation as
    relay-to-prod-stream below: a genuinely new post's Spotify +
    rate-limited MusicBrainz + translation stages can run 60-90s+, and a
    blank page for that long reads as "did this hang?" (see CLAUDE.md).
    GET (not the form's POST) because the browser's built-in EventSource
    only supports GET. The plain POST route stays in place unchanged as a
    no-JS fallback — its formaction is still the form's default action;
    this route just intercepts the click first when JS runs. Unlike
    relay-to-prod-stream, this isn't local-only: it's the same direct
    Medium fetch /admin/add-post always did, just with progress streamed
    instead of blocking.
    """
    q: queue.Queue = queue.Queue()

    def worker():
        conn = get_connection()
        try:
            stats = sync_posts([url], conn, on_progress=q.put)
            q.put(("__done__", stats))
        except Exception as e:
            q.put(("__error__", str(e)))
        finally:
            conn.close()

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "__done__":
                yield f"event: done\ndata: {json.dumps(item[1])}\n\n"
                return
            if isinstance(item, tuple) and item[0] == "__error__":
                yield f"event: error\ndata: {json.dumps(item[1])}\n\n"
                return
            yield f"data: {item}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/admin/relay-to-prod", response_class=HTMLResponse)
def relay_to_prod(url: str = Form(...), _: str = Depends(require_admin)):
    """Browser-facing trigger for relay_add_post.relay_add_post() — lets
    the local dev admin page ('Scrape locally & push to production'
    button) do the same thing the CLI script does, without a terminal.
    Local-only: this process needs to be the one running on a normal
    residential IP for the relay to make sense at all (see
    relay_add_post.py's module docstring), so this 404s if it's ever hit
    on production itself (BASE_PATH set) rather than silently doing a
    pointless prod-fetches-and-POSTs-to-itself round trip.
    """
    if BASE_PATH:
        raise HTTPException(status_code=404)

    from relay_add_post import relay_add_post

    try:
        stats = relay_add_post(url)
    except Exception as e:
        return _admin_page(f"<p style='color:#b00'>Relay to production failed: {html.escape(str(e))}</p>", url)

    if stats["failed_urls"]:
        result = f"<p style='color:#b00'>Production failed to process {url} — check the URL and try again.</p>"
    else:
        result = (
            f"<p>Pushed to production — {stats['new']} new album(s) added"
            f"{' (Spotify/MusicBrainz enrichment ran for those)' if stats['new'] else ''}, "
            f"{stats['updated']} already existed and had their text/media refreshed.</p>"
        )
    return _admin_page(result, url)


@app.get("/admin/relay-to-prod-stream")
def relay_to_prod_stream(url: str, _: str = Depends(require_admin)):
    """SSE version of /admin/relay-to-prod, for the admin page's JS to show
    live progress instead of a blank page for however long a genuinely new
    post's enrichment takes (confirmed 2026-08-18 to run 60-90s+ — see
    CLAUDE.md — long enough that it read as "did this hang?"). GET with a
    query-string url (not the form's POST) because the browser's built-in
    EventSource only supports GET. Same local-only restriction as
    /admin/relay-to-prod, and that plain-POST route stays in place
    unchanged as a no-JS fallback (its formaction is still wired up on the
    button; this route just intercepts the click first when JS runs).
    """
    if BASE_PATH:
        raise HTTPException(status_code=404)

    from relay_add_post import relay_add_post_stream

    def event_stream():
        try:
            for item in relay_add_post_stream(url):
                if isinstance(item, dict):
                    yield f"event: done\ndata: {json.dumps(item)}\n\n"
                else:
                    yield f"data: {item}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/admin/add-post-relay")
def add_post_relay(
    url: str = Form(...), state: str = Form(...), _: str = Depends(require_admin)
):
    """Relay endpoint for relay_add_post.py — used when Medium/Cloudflare
    is challenging requests from this server's own (datacenter) IP, which
    a plain request from a residential IP still gets past. The caller
    fetches the post itself and hands over its Apollo state (the same blob
    fetch_medium_post_state() would have fetched here) so this only needs
    to parse/merge/enrich, no outbound Medium request.

    Streams progress as Server-Sent Events instead of a single blocking
    JSON response — a genuinely new post's Spotify + rate-limited
    MusicBrainz + translation stages can run 60-90s+, and a plain blocking
    response left relay_add_post.py's HTTP client timing out on requests
    that were actually still succeeding here (confirmed 2026-08-18, see
    CLAUDE.md). sync_prefetched_post() runs in a background thread so its
    on_progress callback can push lines onto a queue that this generator
    drains and forwards live; the run ends with a `done` event carrying the
    same stats dict the old JSON response used to return directly, or an
    `error` event if the thread raised.
    """
    q: queue.Queue = queue.Queue()

    def worker():
        conn = get_connection()
        try:
            stats = sync_prefetched_post(url, json.loads(state), conn, on_progress=q.put)
            q.put(("__done__", stats))
        except Exception as e:
            q.put(("__error__", str(e)))
        finally:
            conn.close()

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "__done__":
                yield f"event: done\ndata: {json.dumps(item[1])}\n\n"
                return
            if isinstance(item, tuple) and item[0] == "__error__":
                yield f"event: error\ndata: {json.dumps(item[1])}\n\n"
                return
            yield f"data: {item}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/")
def index(request: Request):
    page = Path("index.html").read_text(encoding="utf-8")
    page = page.replace("<head>", f'<head>\n<base href="{BASE_PATH}/">', 1)

    album_param = request.query_params.get("album")
    if album_param:
        album = _find_album_by_param(album_param, export_from_db())
        if album:
            lang = request.query_params.get("lang", "nl")
            tags = _album_og_tags(album, lang)
            page = _inject_album_meta(page, tags, str(request.url))

    return HTMLResponse(page)


# Mounted by name, not the whole project root — the repo root also has
# .env, the DB file, and the pipeline scripts, none of which should be
# web-servable.
app.mount("/css", StaticFiles(directory="css"), name="css")
app.mount("/js", StaticFiles(directory="js"), name="js")
app.mount("/img", StaticFiles(directory="img"), name="img")
