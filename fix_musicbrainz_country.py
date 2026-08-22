"""
One-off fix: re-resolve mb_country for albums whose value is a city or UK
subdivision instead of an actual country (e.g. Boston's country stored as
"Boston", Pink Floyd's as "England" instead of "United Kingdom") - see
CLAUDE.md. Root cause: musicbrainz_lookup() used to read the artist's raw
`area.name`, which MusicBrainz sets at whatever granularity it happens to
have on file, not necessarily country level. Fixed in enrich_1001_albums.py
to prefer the artist's top-level `country` field (an ISO 3166-1 code
MusicBrainz has already resolved to true country level) via
resolve_country_name(). Also re-checks the handful of albums still at
mb_country IS NULL, in case the fix resolves them too.

Commits after every album, same safe-to-resume pattern as
backfill_musicbrainz.py/backfill_spotify.py.
"""

import json
import time
from pathlib import Path

from db import get_connection, update_album_musicbrainz, export_from_db
from enrich_1001_albums import musicbrainz_lookup, MB_REQUEST_DELAY, OUTPUT_FILE

# Values observed in the DB that are a city/region, not a country - found by
# eyeballing `SELECT DISTINCT mb_country, count(*) FROM albums GROUP BY 1`.
BAD_COUNTRY_VALUES = {
    "England", "Scotland", "Wales", "Northern Ireland",
    "New York", "Boston", "Phoenix", "Memphis", "Los Angeles", "London",
    "Ladysmith",
}


def target_albums(conn):
    placeholders = ",".join("?" * len(BAD_COUNTRY_VALUES))
    return conn.execute(
        f"""
        SELECT id, catalog_number, artist, album, mb_country FROM albums
        WHERE mb_country IN ({placeholders}) OR mb_country IS NULL
        ORDER BY catalog_number
        """,
        tuple(BAD_COUNTRY_VALUES),
    ).fetchall()


def main():
    conn = get_connection()
    albums = target_albums(conn)
    print(f"{len(albums)} albums to re-resolve")

    changed = 0
    for i, a in enumerate(albums, 1):
        label = f"#{a['catalog_number']} {a['artist']} - {a['album']}"
        result = musicbrainz_lookup(a["artist"], a["album"])
        new_country = result.get("country") if result else None
        if new_country != a["mb_country"]:
            update_album_musicbrainz(conn, a["id"], result or {})
            conn.commit()
            changed += 1
            print(f"  [{i}/{len(albums)}] {label}: {a['mb_country']!r} -> {new_country!r}")
        else:
            print(f"  [{i}/{len(albums)}] {label}: unchanged ({new_country!r})")
        time.sleep(MB_REQUEST_DELAY)

    albums_out = export_from_db(conn)
    Path(OUTPUT_FILE).write_text(json.dumps(albums_out, indent=2, ensure_ascii=False), encoding="utf-8")

    conn.close()
    print(f"\nDone. {changed}/{len(albums)} changed.")


if __name__ == "__main__":
    main()
