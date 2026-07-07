"""
1001 Albums project — enrichment pipeline
==========================================

Three stages, run independently or all together:

  1. scrape_medium_posts()   -> pulls header/text/media (YouTube/Spotify/image)
                                 from your Medium posts by parsing the
                                 rendered page's embedded Apollo GraphQL
                                 cache (window.__APOLLO_STATE__) — the old
                                 ?format=json trick stopped working (Medium's
                                 edge cache now ignores that query param)
  2. enrich_with_spotify()   -> finds best-matching Spotify album (handles
                                 remasters/expanded editions by year proximity),
                                 gets embed link + cover art
  3. enrich_with_musicbrainz() -> artist country of origin + genre tags,
                                   cover art fallback via Cover Art Archive

Storage is SQLite (1001albums.db, schema in schema.sql, helpers in db.py) —
re-running main() is incremental: existing albums (matched by composite key
catalog_number+artist+album) keep their already-fetched Spotify/MusicBrainz
data and only get their text/media refreshed from a fresh Medium scrape;
only genuinely new albums go through stages 2 and 3. sync_posts() holds this
logic so a future admin "add by URL" endpoint can call it with a single-URL
list instead of the full medium_post_urls.txt.

main() also regenerates albums_enriched.json from the DB on every run (via
db.export_from_db) so the current static frontend keeps working unchanged
until it's wired up to a live DB-backed route.

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
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv

from db import get_connection, find_album_id, insert_album, update_album_text_media, \
    mark_post_processed, mark_post_failed, export_from_db

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

def extract_js_object(html: str, marker: str) -> str | None:
    """Extract a JS object literal assigned via `marker` (e.g.
    "window.__APOLLO_STATE__ = ") by brace-matching from the first '{' after
    the marker, respecting quoted strings so braces inside string values
    don't throw off the depth count."""
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    depth = 0
    in_str = False
    str_char = ""
    escape = False
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == str_char:
                in_str = False
        else:
            if c in "\"'":
                in_str = True
                str_char = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return html[start:i + 1]
    return None


def fetch_medium_post_state(post_url: str) -> dict:
    """Fetch a Medium post's rendered page and return its embedded Apollo
    GraphQL cache (`window.__APOLLO_STATE__`).

    Replaces the old `?format=json` trick — as of 2026-07 Medium/Cloudflare's
    edge cache ignores that query param entirely (confirmed via
    `CF-Cache-Status: HIT` on every request regardless of cache-busting
    params) and always serves the plain server-rendered HTML page instead of
    the raw JSON payload. The embedded Apollo cache turns out to be a
    strictly better source anyway: it resolves iframe embeds (YouTube/
    Spotify) to their real, playable URL for every post regardless of age —
    unlike the old `thumbnailUrl`/`externalSrc` fields, which only worked for
    recently-created posts and left older posts' embeds unresolvable.
    """
    resp = requests.get(post_url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # Content-Type declares utf-8; pin it rather than
    # trust requests' encoding sniffing, which mangled apostrophes/quotes in
    # review text in an earlier run.
    blob = extract_js_object(resp.text, "window.__APOLLO_STATE__ = ")
    if blob is None:
        raise ValueError(f"could not find window.__APOLLO_STATE__ in {post_url}")
    return json.loads(blob)


def _resolve_ref(state: dict, ref):
    """Apollo cache normalizes entities into a flat dict keyed by
    "Type:id" and replaces nested objects with {"__ref": "Type:id"} pointers
    — resolve one such pointer back to the real entity."""
    if isinstance(ref, dict) and "__ref" in ref:
        return state.get(ref["__ref"])
    return ref


def get_ordered_paragraphs(state: dict) -> list:
    """Resolve Post -> content -> bodyModel.paragraphs (a list of Apollo
    refs) into the actual, ordered list of paragraph entity dicts."""
    post_key = next(k for k in state if k.startswith("Post:"))
    post = state[post_key]
    content_key = next(k for k in post if k.startswith("content("))
    content = _resolve_ref(state, post[content_key])
    para_refs = content["bodyModel"]["paragraphs"]
    return [_resolve_ref(state, ref) for ref in para_refs]


# The Apollo cache represents paragraph `type` as a string, not the integer
# codes the old ?format=json endpoint used. Verified against real posts
# spanning 2019-2026:
#   H3/H4 = post title — observed only ever as paragraph 0. Older posts
#        sometimes tag their title paragraph "P" instead (no distinct heading
#        type at all), so paragraph 0 is unconditionally skipped as the title
#        regardless of its type, rather than relying on the type string.
#   P    = plain paragraph. Per-album headers are ALSO type "P" — there is no
#        way to tell them apart from paragraph type alone. Older posts
#        (pre-~2020) even fuse the header into the start of the review as
#        one paragraph, e.g. "7 Frank Sinatra – Songs for Swingin' Lovers
#        (1956). Daar is Frank weer...".
#   IMG  = image.
#   IFRAME = native iframe embed (YouTube/Spotify), caption in its own
#        `text`. Resolved via iframe.mediaResource -> MediaResource.iframeSrc
#        (an embed.ly wrapper URL whose own `url=` query param is the real,
#        playable source URL) — see resolve_iframe_url().
#   MIXTAPE_EMBED = Medium's auto-generated link-preview card
#        (`mixtapeMetadata.href`). Observed for plain hyperlinks (e.g. a
#        linked news article), not for the album embeds themselves, but its
#        href is directly usable.
TYPE_TITLE = ("H3", "H4")
TYPE_P = "P"
TYPE_IMG = "IMG"
TYPE_IFRAME = "IFRAME"
TYPE_MIXTAPE = "MIXTAPE_EMBED"

# Separator must be an actual dash character (– or —), not a plain hyphen —
# band/album names routinely contain hyphens (e.g. "The Go-Betweens"), which
# would otherwise be mistaken for the artist/album separator. The number is
# sometimes followed by a period or colon instead of just whitespace, e.g.
# post #11's "84. The Beau Brummels – Triangle (1967)." or post #10's
# "76: Astrud Gilberto – Beach Samba (1967)."
HEADER_RE = re.compile(
    r"^\s*(\d{1,4})[.:]?\s+(.*?)\s*[–—]\s*(.*?)\s*\((\d{4})(?:/\d{1,2})?\)\.?\s*(.*)$"
)

def resolve_iframe_url(paragraph: dict, state: dict) -> str | None:
    """Resolve a type=IFRAME paragraph to the embed's real, playable URL.

    `iframe.mediaResource` is an Apollo ref to a MediaResource entity whose
    `iframeSrc` is an embed.ly wrapper URL
    (cdn.embedly.com/widgets/media.html?src=...&url=<real_url>&...) — the
    wrapper's own `url` query param is the real YouTube watch / Spotify
    album URL, and Medium's rendered page always carries it, regardless of
    how old the post is."""
    iframe = paragraph.get("iframe") or {}
    media_resource = _resolve_ref(state, iframe.get("mediaResource"))
    if not media_resource:
        return None
    iframe_src = media_resource.get("iframeSrc")
    if not iframe_src:
        return None
    query = parse_qs(urlparse(iframe_src).query)
    return (query.get("url") or [None])[0]


def classify_media_url(url: str) -> str:
    if "youtube" in url or "youtu.be" in url:
        return "youtube"
    if "spotify" in url or "scdn.co" in url:
        return "spotify"
    return "other"


def parse_medium_post(state: dict) -> list:
    """
    Walk a Medium post's paragraph list and group into album entries.
    Returns a list of dicts: {number, artist, album, year, text, media}
    where media is a LIST of {type, url, caption} — an entry can have more
    than one embed (typically several YouTube clips).
    """
    try:
        paragraphs = get_ordered_paragraphs(state)
    except (StopIteration, KeyError):
        return []

    entries = []
    current = None

    for i, p in enumerate(paragraphs):
        ptype = p.get("type")
        text = p.get("text", "")

        if i == 0 or ptype in TYPE_TITLE:
            continue  # post title — always paragraph 0, regardless of type

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
            image_id = (p.get("metadata") or {}).get("id")
            if image_id:
                current["media"].append({
                    "type": "image",
                    "url": f"https://miro.medium.com/v2/resize:fit:1400/{image_id}",
                    "caption": text or None,
                })

        elif ptype == TYPE_IFRAME:
            url = resolve_iframe_url(p, state)
            if url:
                current["media"].append({
                    "type": classify_media_url(url),
                    "url": url,
                    "caption": text or None,
                })
            # else: the embed's MediaResource couldn't be resolved (rare) —
            # nothing usable to store.

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


def scrape_medium_posts(post_urls: list) -> tuple[list, list]:
    """Scrape a list of Medium post URLs. Returns (entries, failures) — each
    entry is tagged with its source post ("medium_post_url") so it can be
    stored on the album row, and failures is a list of (url, error_message)
    so the caller can mark those posts accordingly rather than silently
    treating them as skipped."""
    all_entries = []
    failures = []
    for url in post_urls:
        print(f"Scraping {url} ...")
        try:
            state = fetch_medium_post_state(url)
            entries = parse_medium_post(state)
            for e in entries:
                e["medium_post_url"] = url
            all_entries.extend(entries)
            print(f"  -> {len(entries)} entries")
        except Exception as e:
            print(f"  ! failed: {e}")
            failures.append((url, str(e)))
        time.sleep(REQUEST_DELAY)
    return all_entries, failures


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
    "collector",
)

def strip_reissue_suffix(name: str) -> str:
    """Strip a trailing parenthetical qualifier from a candidate album name,
    but only if it actually names a reissue (contains a REISSUE_MARKERS
    keyword) — e.g. "Van Halen (Remastered)" -> "Van Halen", but "Document
    (R.E.M. No. 5)" is left alone since that parenthetical isn't a reissue
    marker and might signal a genuinely different release worth a manual
    check. Needed because for a lot of older catalog, Spotify's *only*
    indexed pressing is the remaster/anniversary/deluxe edition — the
    qualifier text alone was tanking the similarity score for otherwise
    correct matches (e.g. "War (Remastered)" scored 0.316 for U2's "War")."""
    result = name
    while True:
        m = re.search(r"\s*\(([^)]*)\)\s*$", result)
        if not m or not any(marker in m.group(1).lower() for marker in REISSUE_MARKERS):
            return result
        result = result[:m.start()]


# Floor on album-title similarity (not the combined artist+album score) below
# which a "best of 10 candidates" result is treated as no match rather than
# trusted. Found necessary after a live test: Fats Domino's "This is Fats"
# (1956) had no real candidate in the API's 10-result cap, but the combined
# score still picked "Fats Is Back" (1968) with high confidence because the
# artist matched exactly (artist_sim=1.0) even though the album title didn't
# (album_sim=0.583) — the combined average masked a wrong album behind a
# right artist. Gating on album_sim alone catches this case (0.583 < 0.65)
# without needing a hand-labeled dataset to calibrate against.
ALBUM_SIM_THRESHOLD = 0.65


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

    def similarities(a):
        candidate_name = strip_reissue_suffix(a.get("name", ""))
        album_sim = difflib.SequenceMatcher(
            None, candidate_name.lower(), album.lower()
        ).ratio()
        candidate_artists = " ".join(ar.get("name", "") for ar in a.get("artists", []))
        artist_sim = difflib.SequenceMatcher(
            None, candidate_artists.lower(), artist.lower()
        ).ratio()
        return album_sim, artist_sim

    def score(a):
        # Name similarity dominates: with results capped at 10 candidates
        # (Development Mode), year-proximity alone was picking wrong-but-
        # close-year albums over the real target, especially for self-titled
        # albums (many different self-titled records by the same artist).
        # Reissue-marked names ("... (Expanded Edition)") are deprioritized
        # in favor of a plain original pressing when both are candidates —
        # reissues often carry the original release year in their own
        # metadata, so year-proximity alone can't tell them apart.
        album_sim, artist_sim = similarities(a)
        is_reissue = any(m in a.get("name", "").lower() for m in REISSUE_MARKERS)
        is_studio_album = a.get("album_type") == "album"
        return (
            -(album_sim + artist_sim) / 2,
            not is_studio_album,
            is_reissue,
            abs(year_of(a) - target_year),
        )

    items.sort(key=score)
    best = items[0]
    best_album_sim, best_artist_sim = similarities(best)

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
        "album_similarity": round(best_album_sim, 3),
        "confident": best_album_sim >= ALBUM_SIM_THRESHOLD,
    }


def enrich_with_spotify(entries: list) -> list:
    token = get_spotify_token()
    for e in entries:
        if any(m["type"] == "spotify" for m in e.get("media", [])):
            continue  # already has a Spotify embed from Medium, skip lookup
        result = spotify_search_album(token, e["artist"], e["album"], e["year"])
        if result and not result["confident"]:
            print(
                f"  ! low-confidence Spotify match, treating as no match: "
                f"{e['artist']} - {e['album']} -> {result['matched_artist_name']} - "
                f"{result['matched_album_name']} (album_similarity={result['album_similarity']})"
            )
            result = None
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
# SYNC — scrape a set of posts and merge into the DB
# ---------------------------------------------------------------------------

def sync_posts(post_urls: list, conn) -> dict:
    """Scrape post_urls, merge into the DB, and run Spotify/MusicBrainz
    enrichment only for genuinely new albums. Shared by main() (the full
    medium_post_urls.txt list) and, later, an admin endpoint that calls this
    with a single newly-added URL — the incremental behavior is identical
    either way, it's just a matter of how many URLs are in the list.

    An album already in the DB (matched by catalog_number+artist+album —
    NOT catalog_number alone, since a handful of posts reuse the same
    catalog number for two different albums) keeps its existing
    spotify/musicbrainz enrichment and only has its header/text/media
    refreshed from the fresh scrape — avoids re-hitting the rate-limited
    MusicBrainz/Spotify APIs for the whole dataset on every run.

    Returns a small stats dict: {"scraped", "new", "updated", "failed_urls"}.
    """
    print(f"=== Stage 1: scraping {len(post_urls)} Medium posts ===")
    scraped, failures = scrape_medium_posts(post_urls)
    print(f"Total album entries scraped: {len(scraped)}")

    new_entries = []
    updated = 0
    for e in scraped:
        album_id = find_album_id(conn, e["number"], e["artist"], e["album"])
        if album_id is not None:
            update_album_text_media(conn, album_id, e)
            updated += 1
        else:
            new_entries.append(e)

    if new_entries:
        print(f"=== Stage 2: Spotify enrichment ({len(new_entries)} new entries) ===")
        enrich_with_spotify(new_entries)

        print(f"=== Stage 3: MusicBrainz enrichment ({len(new_entries)} new entries) ===")
        enrich_with_musicbrainz(new_entries)

        for e in new_entries:
            insert_album(conn, e)
    else:
        print("No new entries — skipping Spotify/MusicBrainz stages.")

    failed_urls = {url for url, _ in failures}
    for url in post_urls:
        if url in failed_urls:
            continue
        mark_post_processed(conn, url)
    for url, error in failures:
        mark_post_failed(conn, url, error)

    conn.commit()

    return {
        "scraped": len(scraped),
        "new": len(new_entries),
        "updated": updated,
        "failed_urls": sorted(failed_urls),
    }


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

    conn = get_connection()
    stats = sync_posts(post_urls, conn)

    albums = export_from_db(conn)
    conn.close()

    Path(OUTPUT_FILE).write_text(
        json.dumps(albums, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Done. {stats['new']} new, {stats['updated']} updated"
        + (f", {len(stats['failed_urls'])} posts failed" if stats["failed_urls"] else "")
        + f". Wrote {len(albums)} entries to {OUTPUT_FILE}."
    )


if __name__ == "__main__":
    main()
