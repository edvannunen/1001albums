"""
1001 Albums project — enrichment pipeline
==========================================

Three stages, run independently or all together:

  1. scrape_medium_posts()   -> pulls header/text/media (YouTube/Spotify/image)
                                 from your Medium posts via the ?format=json trick
  2. enrich_with_spotify()   -> finds best-matching Spotify album (handles
                                 remasters/expanded editions by year proximity),
                                 gets embed link + cover art
  3. enrich_with_musicbrainz() -> artist country of origin + genre tags,
                                   cover art fallback via Cover Art Archive

Output: a single JSON file, one record per album, ready to feed your
searchable/sortable site + dashboard.

SETUP
-----
pip install requests python-dotenv --break-system-packages

Spotify: create an app at https://developer.spotify.com/dashboard
  -> get CLIENT_ID / CLIENT_SECRET, put them in a .env file (see .env.example).
MusicBrainz: no key needed, just a descriptive User-Agent (set in .env).
"""

import json
import os
import re
import time
import base64
import difflib
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

MEDIUM_USERNAME = "edvannunen"          # medium.com/@edvannunen
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
USER_AGENT = os.environ["MUSICBRAINZ_USER_AGENT"]

OUTPUT_FILE = "albums_enriched.json"
POST_URLS_FILE = "medium_post_urls.txt"  # one Medium post URL per line

REQUEST_DELAY = 0.3  # seconds between API calls, be a good citizen


# ---------------------------------------------------------------------------
# STAGE 1 — Medium scraping
# ---------------------------------------------------------------------------

def fetch_medium_post_json(post_url: str) -> dict:
    """Fetch a Medium post's raw JSON via the ?format=json trick."""
    sep = "&" if "?" in post_url else "?"
    url = f"{post_url}{sep}format=json"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    raw = resp.text
    # strip Medium's anti-hijacking prefix: '])}while(1);</x>'
    payload = raw.split("</x>", 1)[1]
    return json.loads(payload)


def parse_medium_post(data: dict) -> list:
    """
    Walk a Medium post's paragraph list and group into album entries.
    Returns a list of dicts: {number, artist, album, year, text, media}
    where media is a LIST of {type, url, caption} — an entry can have more
    than one embed (typically several YouTube clips).

    Caption handling: Medium's card-style embeds (thumbnail, title, channel
    name, "Bekijken op YouTube" button) are MIXTAPE_EMBED paragraphs. The
    caption shown beneath the card is that paragraph's OWN `text` field —
    it's not a separate italic body paragraph. So the caption is read
    directly off the embed paragraph itself, not inferred from formatting
    on a neighboring paragraph. Some embeds have no caption (empty text),
    which is expected — not every clip was captioned.

    Heuristic: a new entry starts at each H3/H4 paragraph matching
    "NNN Artist — Album (Year)".

    NOTE: this is based on Medium's known internal data model, not yet
    verified against this project's actual post JSON (only tested
    conceptually) — confirm paragraph `type` values on 2-3 real posts
    before trusting this at scale, since Medium has used both raw IFRAME
    and MIXTAPE_EMBED paragraph types for embeds over the years and this
    post history spans many years.
    """
    try:
        paragraphs = data["payload"]["value"]["content"]["bodyModel"]["paragraphs"]
        iframes = data["payload"]["value"]["content"]["bodyModel"].get("iframes", {})
    except KeyError:
        return []

    header_re = re.compile(r"^\s*(\d{1,4})\s+(.*?)\s*[—-]\s*(.*?)\s*\((\d{4})\)\s*$")

    entries = []
    current = None

    EMBED_TYPES = ("IFRAME", "MIXTAPE_EMBED")

    for p in paragraphs:
        ptype = p.get("type")
        text = p.get("text", "")

        if ptype in ("H3", "H4"):
            match = header_re.match(text)
            if match:
                if current:
                    entries.append(current)
                current = {
                    "number": match.group(1),
                    "artist": match.group(2),
                    "album": match.group(3),
                    "year": match.group(4),
                    "text": "",
                    "media": [],
                }
                continue

        if current is None:
            continue

        if ptype == "P":
            current["text"] = (current["text"] + " " + text).strip()

        elif ptype == "IMG":
            image_id = p.get("metadata", {}).get("id")
            if image_id:
                current["media"].append({
                    "type": "image",
                    "url": f"https://miro.medium.com/v2/resize:fit:1400/{image_id}",
                    "caption": text or None,
                })

        elif ptype in EMBED_TYPES:
            mixtape = p.get("mixtapeMetadata") or {}
            iframe_ref = p.get("iframe", {})
            media_id = (
                iframe_ref.get("mediaResourceId")
                or mixtape.get("mediaResourceId")
            )
            original_url = mixtape.get("href", "")
            if not original_url and media_id:
                original_url = iframes.get(media_id, {}).get("originalUrl", "")

            if "youtube" in original_url or "youtu.be" in original_url:
                mtype = "youtube"
            elif "spotify" in original_url:
                mtype = "spotify"
            elif original_url:
                mtype = "other"
            else:
                mtype = None

            if mtype:
                current["media"].append({
                    "type": mtype,
                    "url": original_url,
                    "caption": text or None,  # the embed's own text IS the caption
                })

    if current:
        entries.append(current)

    return entries


def scrape_medium_posts(post_urls: list) -> list:
    """Scrape a list of Medium post URLs, return flat list of album entries."""
    all_entries = []
    for url in post_urls:
        print(f"Scraping {url} ...")
        try:
            data = fetch_medium_post_json(url)
            entries = parse_medium_post(data)
            all_entries.extend(entries)
            print(f"  -> {len(entries)} entries")
        except Exception as e:
            print(f"  ! failed: {e}")
        time.sleep(REQUEST_DELAY)
    return all_entries


# ---------------------------------------------------------------------------
# STAGE 2 — Spotify enrichment
# ---------------------------------------------------------------------------

def get_spotify_token() -> str:
    auth = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def spotify_search_album(token: str, artist: str, album: str, year: str) -> dict | None:
    """Search Spotify, pick the candidate closest to the book's release year."""
    headers = {"Authorization": f"Bearer {token}"}
    query = f"album:{album} artist:{artist}"
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        params={"q": query, "type": "album", "limit": 20},
        timeout=15,
    )
    if resp.status_code != 200:
        return None

    items = resp.json().get("albums", {}).get("items", [])
    candidates = [a for a in items if a.get("album_type") == "album"]
    if not candidates:
        candidates = items
    if not candidates:
        return None

    target_year = int(year)

    def year_of(a):
        try:
            return int(a["release_date"][:4])
        except (ValueError, KeyError):
            return 9999

    candidates.sort(key=lambda a: abs(year_of(a) - target_year))
    best = candidates[0]

    return {
        "spotify_url": best["external_urls"]["spotify"],
        "spotify_embed_url": f"https://open.spotify.com/embed/album/{best['id']}",
        "cover_art_url": best["images"][0]["url"] if best.get("images") else None,
        "matched_release_year": year_of(best),
        "exact_year_match": year_of(best) == target_year,
    }


def enrich_with_spotify(entries: list) -> list:
    token = get_spotify_token()
    for e in entries:
        if any(m["type"] == "spotify" for m in e.get("media", [])):
            continue  # already has a Spotify embed from Medium, skip lookup
        result = spotify_search_album(token, e["artist"], e["album"], e["year"])
        e["spotify"] = result
        if not result:
            print(f"  ! no Spotify match: {e['artist']} - {e['album']}")
        time.sleep(REQUEST_DELAY)
    return entries


# ---------------------------------------------------------------------------
# STAGE 3 — MusicBrainz enrichment (country + genre + art fallback)
# ---------------------------------------------------------------------------

LUCENE_SPECIAL_RE = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def lucene_escape(value: str) -> str:
    return LUCENE_SPECIAL_RE.sub(r"\\\1", value)


def musicbrainz_lookup(artist: str, album: str) -> dict:
    headers = {"User-Agent": USER_AGENT}
    query = f'artist:"{lucene_escape(artist)}" AND release:"{lucene_escape(album)}"'
    resp = requests.get(
        "https://musicbrainz.org/ws/2/release-group/",
        headers=headers,
        params={
            "query": query,
            "fmt": "json",
            "limit": 1,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return {}
    groups = resp.json().get("release-groups", [])
    if not groups:
        return {}

    rg = groups[0]
    rg_id = rg["id"]
    artist_credit = rg.get("artist-credit", [])
    artist_id = artist_credit[0]["artist"]["id"] if artist_credit else None

    country = None
    if artist_id:
        time.sleep(REQUEST_DELAY)
        a_resp = requests.get(
            f"https://musicbrainz.org/ws/2/artist/{artist_id}",
            headers=headers,
            params={"fmt": "json"},
            timeout=15,
        )
        if a_resp.status_code == 200:
            country = a_resp.json().get("area", {}).get("name")

    genres = [t["name"] for t in rg.get("tags", [])] if rg.get("tags") else []

    return {
        "country": country,
        "genres": genres,
        "cover_art_archive_url": f"https://coverartarchive.org/release-group/{rg_id}/front",
    }


def enrich_with_musicbrainz(entries: list) -> list:
    for e in entries:
        result = musicbrainz_lookup(e["artist"], e["album"])
        e["musicbrainz"] = result
        time.sleep(REQUEST_DELAY)
    return entries


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    post_urls_path = Path(POST_URLS_FILE)
    if not post_urls_path.exists():
        print(f"Create {POST_URLS_FILE} with one Medium post URL per line, then re-run.")
        return

    post_urls = [
        line.strip() for line in post_urls_path.read_text().splitlines() if line.strip()
    ]

    print(f"=== Stage 1: scraping {len(post_urls)} Medium posts ===")
    entries = scrape_medium_posts(post_urls)
    print(f"Total album entries: {len(entries)}")

    print("=== Stage 2: Spotify enrichment ===")
    entries = enrich_with_spotify(entries)

    print("=== Stage 3: MusicBrainz enrichment ===")
    entries = enrich_with_musicbrainz(entries)

    Path(OUTPUT_FILE).write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"Done. Wrote {len(entries)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
