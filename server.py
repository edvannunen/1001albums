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
  - /admin/ — a single authenticated page (admin.html, a static file served
    through _serve_static_admin_page like the frontend files below): a
    sidebar menu combining post creation/import and the Signal/Reddit/
    Bluesky share tools (previously two separate pages/templates — a
    server-templated form here plus a standalone share_export.html). Fully
    client-side JS, talking to the JSON/SSE endpoints below via relative
    URLs (resolved against the same injected <base> the frontend uses).
    Import ("Scrape & add") calls /admin/add-post-stream, which runs
    sync_posts() (from enrich_1001_albums.py) for just that one URL —
    scrape, merge into the DB, Spotify/MusicBrainz enrichment only for
    genuinely new albums. Create calls /admin/draft-post-preview, which
    generates a title/content draft from data/1001_albums_2018_edition_list.csv
    (the book's full track list) for the next unscraped batch — no Medium
    API involved (this account has no publishing integration token), just
    text for Ed to paste into a new Medium story himself.
  - /admin/add-post-relay — same end result as add-post-stream, but takes
    an already-fetched Apollo state instead of fetching Medium itself.
    Exists because Medium/Cloudflare intermittently challenges requests
    from this server's own (Hetzner datacenter) IP — confirmed 2026-08-18,
    not a fixed block, Cloudflare's bot-fight scoring just flags
    datacenter ASNs on and off — which a plain server-side `requests.get`
    can never pass (it's a JS challenge, not a header check). When the
    Import page's direct fetch fails this way, run
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

import csv
import html
import json
import os
import queue
import re
import secrets
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from db import export_from_db, find_album_id, get_connection, update_album_text
from enrich_1001_albums import (
    extract_post_preview,
    fetch_medium_post_state,
    get_spotify_token,
    spotify_search_album,
    sync_posts,
    sync_prefetched_post,
)
from translate import translate_album_content

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


@app.get("/admin/login")
def admin_login(_: str = Depends(require_admin)):
    """Enables the public site's edit-in-place UI. Visiting this URL
    triggers the browser's native Basic-auth prompt (same as /admin/
    itself) — on success it sets `admin_ui`, a plain non-sensitive cookie
    that is ONLY a UI flag telling index.html to show the edit pencil. It
    grants no access by itself: the real authorization boundary is (and
    stays) Depends(require_admin) on the actual write endpoint below,
    checked server-side on every request, same as everywhere else in this
    app. BASE_PATH-prefixed redirect target for the same reason index()'s
    <base> tag is — see the module docstring above."""
    resp = RedirectResponse(url=f"{BASE_PATH}/")
    resp.set_cookie("admin_ui", "1", max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp


@app.get("/admin/logout")
def admin_logout():
    """Clears the UI-flag cookie. Can't force the browser to forget its
    cached Basic-auth credentials (no API for that) — closing the browser
    is the only real "full" logout, same pre-existing limitation as the
    rest of this single-user admin area."""
    resp = RedirectResponse(url=f"{BASE_PATH}/")
    resp.delete_cookie("admin_ui")
    return resp


@app.post("/admin/update-album-text")
def update_album_text_route(
    number: str = Form(...),
    artist: str = Form(...),
    album: str = Form(...),
    lang: str = Form(...),
    text: str = Form(...),
    _: str = Depends(require_admin),
):
    """Backs the public site's edit-in-place pencil (index.html/js/modal.js).
    lang='nl' also retranslates immediately via translate_album_content()
    (the same per-album primitive translate.py's pipeline pass uses) so the
    English side doesn't go stale until the next full pipeline run — a
    translation hiccup here doesn't fail the save itself, since the DB
    write already committed. translate_missing() is deliberately NOT used
    here: it batch-scans every album still missing a translation, which
    would make a single save unpredictably slow and translate unrelated
    albums as a side effect.
    """
    if lang not in ("nl", "en"):
        raise HTTPException(status_code=400, detail="lang must be 'nl' or 'en'")

    conn = get_connection()
    try:
        album_id = find_album_id(conn, number, artist, album)
        if album_id is None:
            raise HTTPException(status_code=404, detail="album not found")

        update_album_text(conn, album_id, lang, text)

        text_en = None
        if lang == "nl":
            try:
                text_en, _captions_en = translate_album_content(
                    anthropic.Anthropic(), artist, album, text, []
                )
                conn.execute("UPDATE albums SET text_en = ? WHERE id = ?", (text_en, album_id))
                conn.commit()
            except Exception as e:
                print(f"Retranslation failed for {artist} - {album}: {e}")

        row = conn.execute("SELECT text, text_en FROM albums WHERE id=?", (album_id,)).fetchone()
        return JSONResponse({"ok": True, "text": row["text"], "text_en": row["text_en"]})
    finally:
        conn.close()


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


def _serve_static_admin_page(filename: str) -> HTMLResponse:
    """Shared by the /admin/ routes below: read a standalone static HTML
    file and inject the same <base> tag index() uses (these pages fetch
    albums_enriched.json and import js/data.js relatively), plus a
    window.IS_LOCAL flag the page's own JS uses to decide whether to show
    the "Scrape locally & push to production" button — that button only
    makes sense on the process with a normal (non-datacenter) IP Cloudflare
    doesn't challenge, i.e. when BASE_PATH is unset (see the "why this
    exists" note on /admin/relay-to-prod below).
    """
    page = Path(filename).read_text(encoding="utf-8")
    page = page.replace(
        "<head>",
        f'<head>\n<base href="{BASE_PATH}/">\n'
        f'<script>window.IS_LOCAL = {"false" if BASE_PATH else "true"};</script>',
        1,
    )
    return HTMLResponse(page)


@app.get("/admin/", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    """Single combined admin page: create/import a Medium post, plus the
    Signal/Reddit/Bluesky share tools, one sidebar menu instead of two
    separate pages (this route used to render a server-templated form; the
    old share-export tool lived at /admin/share-export). All fully
    client-side JS now (see admin.html) — the plain-POST /admin/add-post
    and /admin/relay-to-prod endpoints below are no-JS-fallback-free as a
    result, but nothing outside this page ever linked to them directly.
    """
    return _serve_static_admin_page("admin.html")


# ---------------------------------------------------------------------------
# Create medium post — draft title/content generator
# ---------------------------------------------------------------------------
# Medium doesn't offer this account a publishing API any more (no
# "Integration tokens" option under Settings -> Security and apps, confirmed
# 2026-09-08), so there's no way to push a real draft into Medium directly.
# This instead generates the title/content text for a new batch of albums,
# for Ed to paste into a new Medium story himself — same "format it, don't
# automate the platform" shape as the Signal/Reddit/Bluesky share tools.

REFERENCE_LIST_PATH = Path("data/1001_albums_2018_edition_list.csv")


@lru_cache(maxsize=1)
def _load_reference_list() -> dict:
    """The full 1001-album book list (catalog number -> artist/album/year),
    used to look up albums that haven't been scraped/imported yet. Distinct
    from the `albums` DB table, which only has albums actually pulled from a
    published Medium post. Cached for the process lifetime — this file is
    static reference data, never written to at runtime.
    """
    result = {}
    with REFERENCE_LIST_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            number = int(row["Nr"])
            result[number] = {
                "artist": row["Artiest"],
                "album": row["Album"],
                "year": int(row["Jaar"]),
            }
    return result


MEDIUM_POST_NUMBER_RE = re.compile(r"1001-albums?-(\d+)")


def _next_medium_post_number(conn) -> int:
    """Medium post numbers aren't stored anywhere explicitly — only each
    album's medium_post_url, which (for every post since #7) embeds the
    post's own number in its URL slug, e.g.
    ".../streetwise-1001-albums-78-642-648-1989-<hash>" -> 78. Takes the max
    across all stored URLs and adds one.
    """
    rows = conn.execute(
        "SELECT DISTINCT medium_post_url FROM albums WHERE medium_post_url IS NOT NULL"
    ).fetchall()
    numbers = [
        int(m.group(1))
        for row in rows
        if (m := MEDIUM_POST_NUMBER_RE.search(row["medium_post_url"]))
    ]
    return (max(numbers) + 1) if numbers else 1


def _year_label(years: list) -> str:
    """Formats the batch's year(s) for the draft title, per Ed's own
    shorthand: a single year stays plain, two years compress to "1988/'89",
    and three or more compress to "2014-'18" (first year, last year's last
    two digits) rather than listing every year in a long batch.
    """
    distinct = sorted(set(years))
    if len(distinct) == 1:
        return str(distinct[0])
    if len(distinct) == 2:
        return f"{distinct[0]}/'{str(distinct[1])[-2:]}"
    return f"{distinct[0]}-'{str(distinct[-1])[-2:]}"


def _pick_cover_image(images: list, target: int = 300) -> str | None:
    """Picks the Spotify image closest to `target` width (Spotify typically
    offers 640/300/64px) rather than the largest — the Create page's cover
    grid and zip download deliberately use modest ~300px images, not the
    full-res 640px one.
    """
    if not images:
        return None
    return min(images, key=lambda im: abs((im.get("width") or 0) - target))["url"]


def _albums_in_range(start: int, count: int) -> list:
    """Shared by draft-post-preview and draft-post-covers below — looks up
    `count` albums starting at catalog number `start` in the reference list.
    """
    reference = _load_reference_list()
    return [
        {"number": number, **reference[number]}
        for number in range(start, start + count)
        if number in reference
    ]


def _fetch_cover_art(albums: list) -> None:
    """Mutates each album dict in `albums` with a `cover_art_url` (or None).
    Split out from draft-post-preview into its own endpoint (below) because
    Spotify search is the slow, rate-limited part — Ed doesn't want it
    re-run on every "Generate" click while he's still tweaking start/count,
    only when he explicitly asks for covers.
    """
    token = None
    token_failed = False
    for a in albums:
        if token_failed:
            a["cover_art_url"] = None
            continue
        try:
            if token is None:
                token = get_spotify_token()
        except Exception:
            token_failed = True
            a["cover_art_url"] = None
            continue
        try:
            match = spotify_search_album(token, a["artist"], a["album"], str(a["year"]))
        except RuntimeError:
            # Spotify rate limit (429) — stop searching the rest of this
            # batch rather than failing it over missing covers.
            token_failed = True
            match = None
        except Exception:
            match = None
        a["cover_art_url"] = _pick_cover_image(match["images"]) if match else None


@app.get("/admin/draft-post-preview")
def draft_post_preview(
    start: int | None = None,
    count: int | None = None,
    post_number: int | None = None,
    _: str = Depends(require_admin),
):
    """Backs the Create section of admin.html. `start`/`count`/`post_number`
    are all optional — omitted ones are computed the same way the page
    prefills them, so a bare GET returns sensible defaults for "the next
    batch". The "..." at the start of the generated title is deliberate:
    Ed writes the post's actual (creative) title prefix by hand afterwards.
    """
    conn = get_connection()
    try:
        if start is None:
            row = conn.execute("SELECT MAX(catalog_number) AS n FROM albums").fetchone()
            start = (row["n"] or 0) + 1
        if post_number is None:
            post_number = _next_medium_post_number(conn)
    finally:
        conn.close()

    count = count or 7
    albums = _albums_in_range(start, count)
    if not albums:
        raise HTTPException(status_code=404, detail="No albums found in that range")

    end = albums[-1]["number"]
    year_label = _year_label([a["year"] for a in albums])
    title = f"...: 1001 Albums #{post_number} ({start}–{end}, {year_label})"

    # Title and album lines share one box (Ed repositions the title inside
    # Medium's own editor himself, rather than pasting it separately into
    # the title field). Three blank lines between entries, per Ed's own
    # spec, as plain <br>s — tried separate <p>&nbsp;</p> paragraphs instead
    # (in case repeated <br> was what Medium's paste sanitizer collapsed),
    # but confirmed 2026-09-08 it collapses both down to a single line
    # break identically, so there's no upside to the extra complexity.
    line_htmls = [
        f'{a["number"]} <b>{html.escape(a["artist"])} — {html.escape(a["album"])}</b> ({a["year"]})'
        for a in albums
    ]
    content_html = (
        html.escape(title) + "<br><br><br><br>" + "<br><br><br><br>".join(line_htmls)
    )
    line_plains = [
        f'{a["number"]} *{a["artist"]} — {a["album"]}* ({a["year"]})'
        for a in albums
    ]
    content_plain = title + "\n\n\n\n" + "\n\n\n\n".join(line_plains)

    return JSONResponse({
        "start": start, "count": count, "post_number": post_number, "end": end,
        "title": title, "content_html": content_html, "content_plain": content_plain,
        "albums": albums,
    })


@app.get("/admin/draft-post-covers")
def draft_post_covers(start: int, count: int, _: str = Depends(require_admin)):
    """Backs the Create section's "Get covers" button — deliberately a
    separate request from draft-post-preview above, triggered only when Ed
    explicitly clicks it, not on every "Generate". Reuses the pipeline's
    own Spotify search/scoring but doesn't persist anything to the DB —
    display/download only, real enrichment still happens later via Import.
    """
    albums = _albums_in_range(start, count)
    if not albums:
        raise HTTPException(status_code=404, detail="No albums found in that range")
    _fetch_cover_art(albums)
    return JSONResponse({"albums": albums})


@app.get("/admin/medium-post-preview")
def medium_post_preview(url: str, _: str = Depends(require_admin)):
    """Backs the Signal section of admin.html — see extract_post_preview()
    for what it returns and why. This direct fetch can hit the same
    Cloudflare datacenter-IP challenge documented for /admin/add-post; when
    that happens, run relay_medium_preview.py locally instead (see its
    docstring) rather than through this route.
    """
    try:
        state = fetch_medium_post_state(url)
        preview = extract_post_preview(state)
    except (StopIteration, KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Could not parse Medium post: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch Medium post: {e}")

    return JSONResponse({**preview, "url": url})


@app.get("/admin/add-post-stream")
def add_post_stream(url: str, _: str = Depends(require_admin)):
    """Backs admin.html's "Scrape & add" button — SSE so progress streams
    live instead of leaving a blank page: a genuinely new post's Spotify +
    rate-limited MusicBrainz + translation stages can run 60-90s+, long
    enough to read as "did this hang?" (see CLAUDE.md). GET (not POST)
    because the browser's built-in EventSource only supports GET. This is
    the same direct Medium fetch as always, just with progress streamed
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


@app.get("/admin/relay-to-prod-stream")
def relay_to_prod_stream(url: str, _: str = Depends(require_admin)):
    """Backs admin.html's "Scrape locally & push to production" button —
    only rendered there when window.IS_LOCAL is true (see
    _serve_static_admin_page). SSE for the same live-progress reason as
    add-post-stream above (confirmed 2026-08-18 to run 60-90s+ — see
    CLAUDE.md). GET with a query-string url because the browser's built-in
    EventSource only supports GET. Local-only: this process needs to be the
    one running on a normal residential IP for the relay to make sense at
    all (see relay_add_post.py's module docstring), so this 404s if it's
    ever hit on production itself (BASE_PATH set) rather than silently
    doing a pointless prod-fetches-and-POSTs-to-itself round trip.
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
