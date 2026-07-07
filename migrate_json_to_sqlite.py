"""
One-time migration: albums_enriched.json -> 1001albums.db (SQLite).

Creates the schema (schema.sql), inserts every album + its media + genre
tags, seeds medium_posts from medium_post_urls.txt (all marked "processed",
since these are the posts already behind the current JSON), then re-exports
the DB back into the same JSON shape and diffs it against the original
field-for-field to prove the round-trip is lossless before anything else
touches this data.

Safe to re-run: deletes and recreates the DB file each time.
"""

import json
import sqlite3
from pathlib import Path

JSON_FILE = "albums_enriched.json"
POST_URLS_FILE = "medium_post_urls.txt"
SCHEMA_FILE = "schema.sql"
DB_FILE = "1001albums.db"


def build_db():
    db_path = Path(DB_FILE)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(DB_FILE)
    conn.executescript(Path(SCHEMA_FILE).read_text(encoding="utf-8"))

    albums = json.loads(Path(JSON_FILE).read_text(encoding="utf-8"))

    for e in albums:
        spotify = e.get("spotify") or {}
        mb = e.get("musicbrainz") or {}

        cur = conn.execute(
            """
            INSERT INTO albums (
                catalog_number, artist, album, year, text,
                spotify_url, spotify_embed_url, spotify_cover_art_url,
                spotify_matched_artist_name, spotify_matched_album_name,
                spotify_matched_release_year, spotify_exact_year_match,
                mb_country, mb_cover_art_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(e["number"]), e["artist"], e["album"], int(e["year"]), e["text"],
                spotify.get("spotify_url"), spotify.get("spotify_embed_url"),
                spotify.get("cover_art_url"), spotify.get("matched_artist_name"),
                spotify.get("matched_album_name"), spotify.get("matched_release_year"),
                None if "exact_year_match" not in spotify else int(spotify["exact_year_match"]),
                mb.get("country"), mb.get("cover_art_archive_url"),
            ),
        )
        album_id = cur.lastrowid

        for pos, m in enumerate(e.get("media") or []):
            conn.execute(
                "INSERT INTO media (album_id, type, url, caption, position) VALUES (?, ?, ?, ?, ?)",
                (album_id, m["type"], m["url"], m.get("caption"), pos),
            )

        for tag in mb.get("genres") or []:
            conn.execute(
                "INSERT INTO genres (album_id, tag) VALUES (?, ?)", (album_id, tag)
            )

    post_urls_path = Path(POST_URLS_FILE)
    if post_urls_path.exists():
        urls = [
            line.strip()
            for line in post_urls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        conn.executemany(
            "INSERT INTO medium_posts (url, status, last_scraped_at) "
            "VALUES (?, 'processed', CURRENT_TIMESTAMP)",
            [(u,) for u in urls],
        )

    conn.commit()
    conn.close()
    print(f"Migrated {len(albums)} albums into {DB_FILE}")


def export_from_db() -> list:
    """Reconstruct the albums_enriched.json shape from the DB, for
    round-trip verification (and later reused as the live /albums_enriched.json
    export route)."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    albums = conn.execute("SELECT * FROM albums ORDER BY id").fetchall()
    media_rows = conn.execute("SELECT * FROM media ORDER BY album_id, position").fetchall()
    genre_rows = conn.execute("SELECT * FROM genres ORDER BY album_id").fetchall()
    conn.close()

    media_by_album = {}
    for m in media_rows:
        media_by_album.setdefault(m["album_id"], []).append(
            {"type": m["type"], "url": m["url"], "caption": m["caption"]}
        )
    genres_by_album = {}
    for g in genre_rows:
        genres_by_album.setdefault(g["album_id"], []).append(g["tag"])

    result = []
    for a in albums:
        has_spotify = a["spotify_url"] is not None
        spotify = None
        if has_spotify:
            spotify = {
                "spotify_url": a["spotify_url"],
                "spotify_embed_url": a["spotify_embed_url"],
                "cover_art_url": a["spotify_cover_art_url"],
                "matched_artist_name": a["spotify_matched_artist_name"],
                "matched_album_name": a["spotify_matched_album_name"],
                "matched_release_year": a["spotify_matched_release_year"],
                "exact_year_match": bool(a["spotify_exact_year_match"]),
            }

        # musicbrainz_lookup() returns {} (not a fully-keyed dict) when no
        # release-group matched at all — cover_art_archive_url is the only
        # field that's unconditionally set whenever a release-group WAS
        # found, so its absence is the exact signal for "no match" (verified
        # against the pre-migration JSON: the two sets coincide exactly).
        if a["mb_cover_art_url"] is None:
            musicbrainz = {}
        else:
            musicbrainz = {
                "country": a["mb_country"],
                "genres": genres_by_album.get(a["id"], []),
                "cover_art_archive_url": a["mb_cover_art_url"],
            }

        result.append({
            "number": str(a["catalog_number"]),
            "artist": a["artist"],
            "album": a["album"],
            "year": str(a["year"]),
            "text": a["text"],
            "media": media_by_album.get(a["id"], []),
            "musicbrainz": musicbrainz,
            "spotify": spotify,
        })
    return result


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
