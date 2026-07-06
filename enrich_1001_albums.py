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


# Medium's ?format=json endpoint represents paragraph `type` as an integer
# code, not the string names (H3/P/IMG/IFRAME) other Medium docs describe.
# Verified against real posts spanning 2019-2026:
#   3  = section/post title — appears once, never a per-album header.
#   1  = plain paragraph. Per-album headers are ALSO type 1, not a distinct
#        heading type — there is no way to tell them apart from paragraph
#        type alone. Older posts (pre-~2020) even fuse the header into the
#        start of the review as one paragraph, e.g. "7 Frank Sinatra –
#        Songs for Swingin' Lovers (1956). Daar is Frank weer...".
#   4  = image.
#   11 = native iframe embed (YouTube/Spotify), caption in its own `text`.
#        `iframe.thumbnailUrl` / `iframe.externalSrc` are only populated for
#        recently-created posts — for older posts both are empty strings,
#        so the embed's source URL usually cannot be recovered at all.
#   14 = Medium's auto-generated link-preview card (`mixtapeMetadata.href`).
#        Observed for plain hyperlinks (e.g. a linked news article), not for
#        the album embeds themselves, but its href is directly usable.
TYPE_TITLE = 3
TYPE_P = 1
TYPE_IMG = 4
TYPE_IFRAME = 11
TYPE_MIXTAPE = 14

# Separator must be an actual dash character (– or —), not a plain hyphen —
# band/album names routinely contain hyphens (e.g. "The Go-Betweens"), which
# would otherwise be mistaken for the artist/album separator. The number is
# sometimes followed by a period or colon instead of just whitespace, e.g.
# post #11's "84. The Beau Brummels – Triangle (1967)." or post #10's
# "76: Astrud Gilberto – Beach Samba (1967)."
HEADER_RE = re.compile(
    r"^\s*(\d{1,4})[.:]?\s+(.*?)\s*[–—]\s*(.*?)\s*\((\d{4})(?:/\d{1,2})?\)\.?\s*(.*)$"
)

YOUTUBE_THUMBNAIL_RE = re.compile(r"ytimg\.com/vi/([^/]+)/")


def resolve_iframe_url(iframe: dict) -> str | None:
    """Best-effort resolution of a native iframe embed's source URL. Medium
    only retains thumbnailUrl/externalSrc for recently-created posts; for
    older ones both are empty and the source can't be recovered here."""
    external_src = iframe.get("externalSrc")
    if external_src:
        return external_src
    match = YOUTUBE_THUMBNAIL_RE.search(iframe.get("thumbnailUrl", ""))
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


def classify_media_url(url: str) -> str:
    if "youtube" in url or "youtu.be" in url:
        return "youtube"
    if "spotify" in url or "scdn.co" in url:
        return "spotify"
    return "other"


def parse_medium_post(data: dict) -> list:
    """
    Walk a Medium post's paragraph list and group into album entries.
    Returns a list of dicts: {number, artist, album, year, text, media}
    where media is a LIST of {type, url, caption} — an entry can have more
    than one embed (typically several YouTube clips).
    """
    try:
        paragraphs = data["payload"]["value"]["content"]["bodyModel"]["paragraphs"]
    except KeyError:
        return []

    entries = []
    current = None

    for p in paragraphs:
        ptype = p.get("type")
        text = p.get("text", "")

        if ptype == TYPE_TITLE:
            continue

        if ptype == TYPE_P:
            match = HEADER_RE.match(text)
            if match:
                if current:
                    entries.append(current)
                current = {
                    "number": match.group(1),
                    "artist": match.group(2),
                    "album": match.group(3),
                    "year": match.group(4),
                    "text": match.group(5).strip(),
                    "media": [],
                }
            elif current is not None:
                current["text"] = (current["text"] + " " + text).strip()
            continue

        if current is None:
            continue  # embeds/images before the first recognized album header

        if ptype == TYPE_IMG:
            image_id = p.get("metadata", {}).get("id")
            if image_id:
                current["media"].append({
                    "type": "image",
                    "url": f"https://miro.medium.com/v2/resize:fit:1400/{image_id}",
                    "caption": text or None,
                })

        elif ptype == TYPE_IFRAME:
            url = resolve_iframe_url(p.get("iframe", {}))
            if url:
                current["media"].append({
                    "type": classify_media_url(url),
                    "url": url,
                    "caption": text or None,
                })
            # else: Medium didn't retain enough data to resolve this embed's
            # source (common for older posts) — nothing usable to store.

        elif ptype == TYPE_MIXTAPE:
            url = (p.get("mixtapeMetadata") or {}).get("href")
            if url:
                current["media"].append({
                    "type": classify_media_url(url),
                    "url": url,
                    "caption": text or None,
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

REISSUE_MARKERS = (
    "deluxe",
    "expanded",
    "remaster",
    "anniversary",
    "special edition",
    "bonus track",
    "legacy edition",
)


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
    """Search Spotify and pick the best-matching candidate by name similarity,
    tie-broken by release-year proximity to the book's edition.

    Uses a plain free-text query rather than Spotify's `artist:X album:Y`
    field-filtered syntax, which proved unreliable in several ways: it
    silently returns zero results for names containing an apostrophe (e.g.
    "Cosmo's Factory") even when quoted, and Spotify inconsistently strips a
    leading "The" from some artist names in its own metadata (e.g. "The
    Sisters of Mercy" is indexed as just "Sisters of Mercy"), which an exact
    field match can't tolerate. A free-text query plus our own similarity
    scoring handles both cases, since fuzzy matching doesn't care about exact
    field equality.
    """
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        # Spotify caps `limit` at 10 for apps in Development Mode (confirmed
        # empirically: 15+ returns 400 "Invalid limit") even though the docs
        # say up to 50 — this app hasn't been through Extended Quota review.
        params={"q": f"{artist} {album}", "type": "album", "limit": 10},
        timeout=15,
    )
    if resp.status_code == 429:
        # Fail loudly and stop immediately rather than silently returning
        # None for every remaining album in the batch (a 23-hour Retry-After
        # was observed once, from too many calls across repeated test runs
        # in a short window) - a quiet per-entry failure here would produce
        # hundreds of bogus "no match" results before anyone noticed.
        retry_after = resp.headers.get("Retry-After", "unknown")
        raise RuntimeError(
            f"Spotify rate limit hit (429). Retry-After: {retry_after}s. "
            "Stopping here instead of burning through the rest of the batch."
        )
    if resp.status_code != 200:
        return None

    items = resp.json().get("albums", {}).get("items", [])
    if not items:
        return None

    target_year = int(year)

    def year_of(a):
        try:
            return int(a["release_date"][:4])
        except (ValueError, KeyError):
            return 9999

    def name_similarity(a):
        album_sim = difflib.SequenceMatcher(
            None, a.get("name", "").lower(), album.lower()
        ).ratio()
        candidate_artists = " ".join(ar.get("name", "") for ar in a.get("artists", []))
        artist_sim = difflib.SequenceMatcher(
            None, candidate_artists.lower(), artist.lower()
        ).ratio()
        return (album_sim + artist_sim) / 2

    def score(a):
        # Name similarity dominates: with results capped at 10 candidates
        # (Development Mode), year-proximity alone was picking wrong-but-
        # close-year albums over the real target, especially for self-titled
        # albums (many different self-titled records by the same artist).
        # Reissue-marked names ("... (Expanded Edition)") are deprioritized
        # in favor of a plain original pressing when both are candidates —
        # reissues often carry the original release year in their own
        # metadata, so year-proximity alone can't tell them apart.
        is_reissue = any(m in a.get("name", "").lower() for m in REISSUE_MARKERS)
        is_studio_album = a.get("album_type") == "album"
        return (
            -name_similarity(a),
            not is_studio_album,
            is_reissue,
            abs(year_of(a) - target_year),
        )

    items.sort(key=score)
    best = items[0]

    return {
        "spotify_url": best["external_urls"]["spotify"],
        "spotify_embed_url": f"https://open.spotify.com/embed/album/{best['id']}",
        "cover_art_url": best["images"][0]["url"] if best.get("images") else None,
        "matched_artist_name": ", ".join(
            ar.get("name", "") for ar in best.get("artists", [])
        ),
        "matched_album_name": best.get("name"),
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
            # .get("area", {}) only falls back to {} when the key is absent -
            # MusicBrainz returns "area": null (present, but None) for artists
            # with no known area, which then crashes .get("name") on None.
            country = (a_resp.json().get("area") or {}).get("name")

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
        line.strip()
        for line in post_urls_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    print(f"=== Stage 1: scraping {len(post_urls)} Medium posts ===")
    entries = scrape_medium_posts(post_urls)
    print(f"Total album entries: {len(entries)}")

    print("=== Stage 2: Spotify enrichment ===")
    entries = enrich_with_spotify(entries)

    print("=== Stage 3: MusicBrainz enrichment ===")
    entries = enrich_with_musicbrainz(entries)

    Path(OUTPUT_FILE).write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Done. Wrote {len(entries)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
