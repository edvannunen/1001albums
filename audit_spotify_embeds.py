"""
One-off audit: entries with spotify=None whose Medium post already embedded
a Spotify link. enrich_with_spotify() deliberately skips its own search for
these (see CLAUDE.md), which means their cover art falls back to
MusicBrainz's release-group art — and that fallback is occasionally wrong
(confirmed for #268 War and #508 Thriller). Since we already know the exact
Spotify entity Medium linked to, resolve it directly (album, or track ->
its album) via the real Spotify API instead of a fuzzy search, and report
which ones look safe to backfill vs. need a manual look.

Read-only: fetches from Spotify, does not write to the DB or JSON. Run
apply_spotify_embed_backfill.py (writes both) after reviewing this output.
"""

import difflib
import json
import re
import time

import requests

from enrich_1001_albums import (
    ALBUM_SIM_THRESHOLD,
    REQUEST_DELAY,
    get_spotify_token,
    strip_reissue_suffix,
)

EMBED_URL_RE = re.compile(r"open\.spotify\.com/(album|track|episode)/([A-Za-z0-9]+)")


def similarity(entry_name: str, candidate_name: str) -> float:
    """Mirrors enrich_1001_albums.py's similarities(): the reissue suffix
    ("(Remastered)", "(Deluxe Edition)", ...) lives on the Spotify candidate
    side, not the entry's own plain title — stripping the wrong side is why
    a first pass here flagged nearly everything as low-similarity."""
    return difflib.SequenceMatcher(
        None, strip_reissue_suffix(candidate_name).lower(), entry_name.lower()
    ).ratio()


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def get_json(url, headers):
    """Multi-id batch endpoints (?ids=a,b,c) 403 for this app (same Dev Mode
    restriction as the search `limit` cap noted in CLAUDE.md) — single-item
    lookups work fine, just slower."""
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r.json()


def main():
    data = json.load(open("albums_enriched.json", encoding="utf-8"))

    candidates = []  # (entry, entity_type, entity_id)
    no_embed = []
    unresolvable_type = []
    multi_link = []  # entries with >1 Spotify media link -- can't safely
    # guess which one is the album; several posts embed a second link that's
    # just an aside track or a different, deliberately-referenced album (see
    # #37, #60, #78, #289, #411 -- confirmed by manual review 2026-07-18).
    # Report these separately instead of silently resolving media[0].

    for e in data:
        if e["spotify"] is not None:
            continue
        spotify_media = [m for m in e["media"] if m["type"] == "spotify"]
        if not spotify_media:
            no_embed.append(e)
            continue
        if len(spotify_media) > 1:
            multi_link.append(e)
            continue
        m = EMBED_URL_RE.search(spotify_media[0]["url"])
        if not m:
            unresolvable_type.append(e)
            continue
        kind, sid = m.groups()
        if kind == "episode":
            unresolvable_type.append(e)
            continue
        candidates.append((e, kind, sid))

    print(f"spotify:null total: {sum(1 for e in data if e['spotify'] is None)}")
    print(f"  no Spotify embed at all (genuinely not on Spotify): {len(no_embed)}")
    for e in no_embed:
        print(f"    #{e['number']} {e['artist']} - {e['album']}")
    print(f"  embed type we can't resolve (e.g. podcast episode): {len(unresolvable_type)}")
    for e in unresolvable_type:
        url = [m["url"] for m in e["media"] if m["type"] == "spotify"][0]
        print(f"    #{e['number']} {e['artist']} - {e['album']} -> {url}")
    print(f"  multiple Spotify links, needs manual pick: {len(multi_link)}")
    for e in multi_link:
        urls = [m["url"] for m in e["media"] if m["type"] == "spotify"]
        print(f"    #{e['number']} {e['artist']} - {e['album']} -> {urls}")
    print(f"  resolvable album/track embeds: {len(candidates)}")

    token = get_spotify_token()
    headers = {"Authorization": f"Bearer {token}"}

    track_candidates = [(e, sid) for e, kind, sid in candidates if kind == "track"]
    album_candidates = [(e, sid) for e, kind, sid in candidates if kind == "album"]

    # Resolve track -> album id, one at a time (batch endpoint 403s)
    track_to_album = {}
    for i, (_, sid) in enumerate(track_candidates):
        try:
            t = get_json(f"https://api.spotify.com/v1/tracks/{sid}", headers)
            track_to_album[sid] = t["album"]["id"]
        except requests.exceptions.HTTPError as err:
            print(f"  ! track fetch failed for {sid}: {err}")
        if (i + 1) % 25 == 0:
            print(f"  ...resolved {i+1}/{len(track_candidates)} tracks")

    all_album_ids = sorted(set(sid for _, sid in album_candidates) | set(track_to_album.values()))

    # Fetch full album objects, one at a time
    albums_by_id = {}
    for i, aid in enumerate(all_album_ids):
        try:
            albums_by_id[aid] = get_json(f"https://api.spotify.com/v1/albums/{aid}", headers)
        except requests.exceptions.HTTPError as err:
            print(f"  ! album fetch failed for {aid}: {err}")
        if (i + 1) % 25 == 0:
            print(f"  ...fetched {i+1}/{len(all_album_ids)} albums")

    results = []
    for e, kind, sid in candidates:
        album_id = sid if kind == "album" else track_to_album.get(sid)
        album = albums_by_id.get(album_id) if album_id else None
        if not album:
            results.append({"entry": e, "status": "fetch_failed"})
            continue

        album_name = album["name"]
        artist_name = ", ".join(a["name"] for a in album["artists"])
        album_sim = similarity(e["album"], album_name)
        artist_sim = similarity(e["artist"], artist_name)
        release_year = int(album["release_date"][:4])
        cover_url = album["images"][0]["url"] if album.get("images") else None
        mb_cover = e["musicbrainz"]["cover_art_archive_url"] if e.get("musicbrainz") else None

        confident = album_sim >= ALBUM_SIM_THRESHOLD
        results.append({
            "entry": e,
            "status": "confident" if confident else "low_similarity",
            "album_sim": round(album_sim, 3),
            "artist_sim": round(artist_sim, 3),
            "spotify_url": album["external_urls"]["spotify"],
            "spotify_embed_url": f"https://open.spotify.com/embed/album/{album['id']}",
            "cover_art_url": cover_url,
            "matched_artist_name": artist_name,
            "matched_album_name": album_name,
            "matched_release_year": release_year,
            "exact_year_match": release_year == int(e["year"]),
            "cover_changes": cover_url != mb_cover,
        })

    confident = [r for r in results if r["status"] == "confident"]
    low_sim = [r for r in results if r["status"] == "low_similarity"]
    failed = [r for r in results if r["status"] == "fetch_failed"]
    cover_changes = [r for r in confident if r["cover_changes"]]

    print()
    print(f"resolved via API: {len(results)}")
    print(f"  confident (album_sim >= {ALBUM_SIM_THRESHOLD}): {len(confident)}")
    print(f"    of those, cover art will actually change from current MB fallback: {len(cover_changes)}")
    print(f"  low-similarity, needs manual review: {len(low_sim)}")
    print(f"  fetch failed (dead/removed Spotify id): {len(failed)}")

    print()
    print("=== LOW SIMILARITY (manual review) ===")
    for r in low_sim:
        e = r["entry"]
        print(f"  #{e['number']} {e['artist']} - {e['album']} ({e['year']})")
        print(f"      -> Spotify: {r['matched_artist_name']} - {r['matched_album_name']} "
              f"({r['matched_release_year']}) album_sim={r['album_sim']} artist_sim={r['artist_sim']}")
        print(f"      {r['spotify_url']}")

    print()
    print("=== FETCH FAILED ===")
    for r in failed:
        e = r["entry"]
        url = [m["url"] for m in e["media"] if m["type"] == "spotify"][0]
        print(f"  #{e['number']} {e['artist']} - {e['album']} -> {url}")

    # Save full results for the apply step so it doesn't need to re-fetch.
    with open("audit_spotify_embeds_results.json", "w", encoding="utf-8") as f:
        json.dump(
            [
                {**{k: v for k, v in r.items() if k != "entry"},
                 "number": r["entry"]["number"], "artist": r["entry"]["artist"], "album": r["entry"]["album"]}
                for r in results
            ],
            f, ensure_ascii=False, indent=2,
        )
    print()
    print("Full results (incl. confident ones) written to audit_spotify_embeds_results.json")


if __name__ == "__main__":
    main()
