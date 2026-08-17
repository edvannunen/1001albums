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

Run locally: uvicorn server:app --reload --port 8000, then browse
http://localhost:8000/ — BASE_PATH is unset/empty locally, so <base
href="/"> is a no-op and everything resolves exactly as it did before this
fix was needed.
Auth: HTTP Basic, single shared username/password from .env
(ADMIN_USERNAME/ADMIN_PASSWORD) — single-user tool, no per-user accounts.
"""

import html
import os
import re
import secrets
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from db import export_from_db, get_connection
from enrich_1001_albums import sync_posts

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
  <form method="post" action="admin/add-post">
    <input name="url" type="url" placeholder="https://edvannunen.medium.com/..."
           style="width:100%; padding:0.5rem; box-sizing:border-box;" required>
    <button type="submit" style="margin-top:0.75rem; padding:0.5rem 1rem;">Scrape &amp; add</button>
  </form>
  {result}
</body>
</html>"""


@app.get("/admin/", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    return ADMIN_PAGE.format(base=BASE_PATH, result="")


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
    return ADMIN_PAGE.format(base=BASE_PATH, result=result)


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
