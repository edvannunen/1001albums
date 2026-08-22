"""
One-off backfill: retry MusicBrainz lookup for albums that ended up with
zero MusicBrainz data at all (no country, no genre tags) - 48 of them as of
2026-08-22, including obvious catalog albums like the Beatles' Revolver and
Sgt Pepper's, which are unambiguously in MusicBrainz. Root cause: the
original enrich_with_musicbrainz() run fired requests faster than
MusicBrainz's ~1 req/sec limit (REQUEST_DELAY=0.3s, shared with Spotify) and
treated any non-200 response identically to a genuine "no match" - a
transient 503 permanently looked the same as "this album isn't in
MusicBrainz." Fixed in enrich_1001_albums.py (MB_REQUEST_DELAY + retry with
backoff + a fuzzy fallback query for near-title-mismatches); this script
re-runs the fixed lookup against just the gap albums.

Commits after every single album, same safe-to-resume pattern as
backfill_spotify.py - re-running only ever re-selects albums still missing
both mb_country and any genre row.
"""

import json
import time
from pathlib import Path

from db import get_connection, update_album_musicbrainz, export_from_db
from enrich_1001_albums import musicbrainz_lookup, MB_REQUEST_DELAY, OUTPUT_FILE


def gap_albums(conn):
    return conn.execute(
        """
        SELECT id, catalog_number, artist, album FROM albums
        WHERE mb_country IS NULL
        AND id NOT IN (SELECT album_id FROM genres)
        ORDER BY catalog_number
        """
    ).fetchall()


def main():
    conn = get_connection()
    albums = gap_albums(conn)
    print(f"{len(albums)} albums need a MusicBrainz retry")

    found = 0
    still_missing = []

    for i, a in enumerate(albums, 1):
        label = f"#{a['catalog_number']} {a['artist']} - {a['album']}"
        result = musicbrainz_lookup(a["artist"], a["album"])
        if result:
            update_album_musicbrainz(conn, a["id"], result)
            conn.commit()
            found += 1
            print(f"  [{i}/{len(albums)}] OK  {label} -> {result.get('country')}, "
                  f"{len(result.get('genres') or [])} genres")
        else:
            still_missing.append(label)
            print(f"  [{i}/{len(albums)}] --  {label} -> still no match")
        time.sleep(MB_REQUEST_DELAY)

    albums_out = export_from_db(conn)
    Path(OUTPUT_FILE).write_text(json.dumps(albums_out, indent=2, ensure_ascii=False), encoding="utf-8")

    conn.close()
    print(f"\nDone. {found} filled in, {len(still_missing)} still genuinely unmatched:")
    for label in still_missing:
        print(f"  - {label}")


if __name__ == "__main__":
    main()
