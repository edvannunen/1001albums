"""
One-time migration: albums_enriched.json -> 1001albums.db (SQLite).

Creates the schema (schema.sql), inserts every album + its media + genre
tags via db.py's shared insert_album(), seeds medium_posts from
medium_post_urls.txt (all marked "processed", since these are the posts
already behind the current JSON), then re-exports the DB back into the
same JSON shape (db.py's export_from_db()) and diffs it against the
original field-for-field to prove the round-trip is lossless before
anything else touches this data.

Safe to re-run: deletes and recreates the DB file each time.
"""

import json
from pathlib import Path

from db import DB_FILE, get_connection, insert_album, export_from_db, mark_post_processed

JSON_FILE = "albums_enriched.json"
POST_URLS_FILE = "medium_post_urls.txt"


def build_db():
    db_path = Path(DB_FILE)
    if db_path.exists():
        db_path.unlink()

    conn = get_connection()  # creates schema, since the file didn't exist

    albums = json.loads(Path(JSON_FILE).read_text(encoding="utf-8"))
    for e in albums:
        insert_album(conn, e)

    post_urls_path = Path(POST_URLS_FILE)
    if post_urls_path.exists():
        urls = [
            line.strip()
            for line in post_urls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for url in urls:
            mark_post_processed(conn, url)

    conn.commit()
    conn.close()
    print(f"Migrated {len(albums)} albums into {DB_FILE}")


def verify():
    original = json.loads(Path(JSON_FILE).read_text(encoding="utf-8"))
    reexported = export_from_db()

    # A pre-existing quirk in albums_enriched.json: enrich_with_spotify()
    # skips entries that already have a Spotify-type media item from Medium
    # WITHOUT setting e["spotify"] = None, so those two entries (#18, #399,
    # added by an earlier one-off merge) are missing the "spotify" key
    # entirely rather than having it explicitly null. The DB export always
    # emits an explicit null, matching the other 619 entries — a
    # normalization, not data loss, so backfill it before comparing.
    for e in original:
        e.setdefault("spotify", None)

    original_by_key = {(e["number"], e["artist"], e["album"]): e for e in original}
    reexported_by_key = {(e["number"], e["artist"], e["album"]): e for e in reexported}

    if len(original) != len(reexported):
        print(f"FAIL: count mismatch — original {len(original)}, re-exported {len(reexported)}")
        return False

    if set(original_by_key) != set(reexported_by_key):
        print("FAIL: key set mismatch")
        print("  only in original:", set(original_by_key) - set(reexported_by_key))
        print("  only in re-export:", set(reexported_by_key) - set(original_by_key))
        return False

    mismatches = []
    for key, orig_e in original_by_key.items():
        new_e = reexported_by_key[key]
        if orig_e != new_e:
            mismatches.append((key, orig_e, new_e))

    if mismatches:
        print(f"FAIL: {len(mismatches)} entries differ after round-trip")
        for key, orig_e, new_e in mismatches[:5]:
            print(f"  --- {key} ---")
            for field in orig_e:
                if orig_e.get(field) != new_e.get(field):
                    print(f"    {field}: {orig_e.get(field)!r} != {new_e.get(field)!r}")
        return False

    print(f"PASS: all {len(original)} entries match field-for-field after round-trip")
    return True


if __name__ == "__main__":
    build_db()
    ok = verify()
    if not ok:
        raise SystemExit(1)
