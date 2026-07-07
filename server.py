"""
1001 Albums backend — FastAPI app. This is the one process meant to run in
production (Coolify) and locally: it serves the static frontend, the live
data route, and the admin page, so local testing matches production
exactly instead of the old two-piece "static file server + separate API"
setup.

  - GET / and /css, /js, /img — the existing static frontend (index.html +
    js/*.js + css/styles.css + img assets), unchanged from the pure-static
    version.
  - GET /albums_enriched.json — live export straight from the DB
    (db.export_from_db), same shape the pipeline has always produced.
    js/data.js's fetch("albums_enriched.json") is a *relative* URL, so once
    the page itself is served from here it resolves to this route
    automatically — no frontend code changes needed.
  - /admin — a single authenticated page: paste a new Medium post URL, it
    runs sync_posts() (from enrich_1001_albums.py) for just that one URL —
    scrape, merge into the DB, Spotify/MusicBrainz enrichment only for
    genuinely new albums. Same incremental logic the bulk pipeline run uses,
    just scoped to one URL instead of the whole medium_post_urls.txt list.

Run locally: uvicorn server:app --reload --port 8000
  (replaces the old `python -m http.server` — this one process now serves
  everything the static server did, plus live data and /admin)
Auth: HTTP Basic, single shared username/password from .env
(ADMIN_USERNAME/ADMIN_PASSWORD) — single-user tool, no per-user accounts.
"""

import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from db import export_from_db, get_connection
from enrich_1001_albums import sync_posts

load_dotenv()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

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


ADMIN_PAGE = """<!doctype html>
<html>
<head><title>1001 Albums admin</title></head>
<body style="font-family: sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem;">
  <h1>Add a Medium post</h1>
  <form method="post" action="/admin/add-post">
    <input name="url" type="url" placeholder="https://edvannunen.medium.com/..."
           style="width:100%; padding:0.5rem; box-sizing:border-box;" required>
    <button type="submit" style="margin-top:0.75rem; padding:0.5rem 1rem;">Scrape &amp; add</button>
  </form>
  {result}
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    return ADMIN_PAGE.format(result="")


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
    return ADMIN_PAGE.format(result=result)


@app.get("/")
def index():
    return FileResponse("index.html")


# Mounted by name, not the whole project root — the repo root also has
# .env, the DB file, and the pipeline scripts, none of which should be
# web-servable.
app.mount("/css", StaticFiles(directory="css"), name="css")
app.mount("/js", StaticFiles(directory="js"), name="js")
app.mount("/img", StaticFiles(directory="img"), name="img")
