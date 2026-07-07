"""
1001 Albums backend — FastAPI app. This is the one process meant to run in
production (Coolify) and locally: it serves the static frontend, the live
data route, and the admin page, so local testing matches production
exactly instead of the old two-piece "static file server + separate API"
setup.

Deployed at https://bier-en-brood.nl/1001albums — a literal subpath, not a
subdomain, shared with other projects on the same domain (De Sprong is the
other one, at /de-sprong). Every route below is registered under BASE_PATH
for that reason: unlike a subdomain, Traefik/Coolify forwards the full
"/1001albums/..." path to this container rather than stripping the prefix
(confirmed by De Sprong needing the equivalent `paths.base` config), so the
app has to know its own mount point. index.html's asset paths stay plain
relative ("css/styles.css", not "/css/styles.css") and resolve correctly
regardless of BASE_PATH, AS LONG AS the browser's URL for the page itself
ends in a trailing slash — Starlette's default redirect_slashes behavior
handles a bare "/1001albums" (no trailing slash) by redirecting to
"/1001albums/" before any relative asset path gets resolved.

  - GET {BASE_PATH}/ and /css, /js, /img — the existing static frontend
    (index.html + js/*.js + css/styles.css + img assets), unchanged from
    the pure-static version.
  - GET {BASE_PATH}/albums_enriched.json — live export straight from the DB
    (db.export_from_db), same shape the pipeline has always produced.
    js/data.js's fetch("albums_enriched.json") is a *relative* URL, so once
    the page itself is served from here it resolves to this route
    automatically — no frontend code changes needed.
  - {BASE_PATH}/admin — a single authenticated page: paste a new Medium
    post URL, it runs sync_posts() (from enrich_1001_albums.py) for just
    that one URL — scrape, merge into the DB, Spotify/MusicBrainz
    enrichment only for genuinely new albums. Same incremental logic the
    bulk pipeline run uses, just scoped to one URL instead of the whole
    medium_post_urls.txt list.

Run locally: uvicorn server:app --reload --port 8000
  then browse http://localhost:8000/1001albums/ — same BASE_PATH locally
  and in production, so local testing is representative.
Auth: HTTP Basic, single shared username/password from .env
(ADMIN_USERNAME/ADMIN_PASSWORD) — single-user tool, no per-user accounts.
"""

import os
import secrets

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from db import export_from_db, get_connection
from enrich_1001_albums import sync_posts

load_dotenv()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
BASE_PATH = "/1001albums"

app = FastAPI()
router = APIRouter(prefix=BASE_PATH)
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


@router.get("/albums_enriched.json")
def albums_json():
    return JSONResponse(export_from_db())


# Form action is a fully-qualified path (including BASE_PATH) rather than a
# relative one — this HTML is served at BASE_PATH/admin (no trailing
# slash), where a plain relative "add-post" would resolve one level too
# high (against BASE_PATH/, not BASE_PATH/admin/). Absolute-with-prefix
# sidesteps that ambiguity entirely.
ADMIN_PAGE = f"""<!doctype html>
<html>
<head><title>1001 Albums admin</title></head>
<body style="font-family: sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem;">
  <h1>Add a Medium post</h1>
  <form method="post" action="{BASE_PATH}/admin/add-post">
    <input name="url" type="url" placeholder="https://edvannunen.medium.com/..."
           style="width:100%; padding:0.5rem; box-sizing:border-box;" required>
    <button type="submit" style="margin-top:0.75rem; padding:0.5rem 1rem;">Scrape &amp; add</button>
  </form>
  {{result}}
</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    return ADMIN_PAGE.format(result="")


@router.post("/admin/add-post", response_class=HTMLResponse)
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
    return ADMIN_PAGE.format(result=result)


@router.get("/")
def index():
    return FileResponse("index.html")


app.include_router(router)

# Mounted by name, not the whole project root — the repo root also has
# .env, the DB file, and the pipeline scripts, none of which should be
# web-servable.
app.mount(f"{BASE_PATH}/css", StaticFiles(directory="css"), name="css")
app.mount(f"{BASE_PATH}/js", StaticFiles(directory="js"), name="js")
app.mount(f"{BASE_PATH}/img", StaticFiles(directory="img"), name="img")
