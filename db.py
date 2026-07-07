"""
Shared SQLite helpers for the 1001 Albums pipeline and (future) admin
backend. Schema lives in schema.sql; this module is the only place that
knows how to translate between a DB row and the albums_enriched.json
entry shape, so the pipeline, the migration script, and any future API
route all stay consistent.
"""

import sqlite3
from pathlib import Path

DB_FILE = "1001albums.db"
SCHEMA_FILE = "schema.sql"


def get_connection() -> sqlite3.Connection:
    """Open the DB, creating the schema on first use."""
    is_new = not Path(DB_FILE).exists()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if is_new:
        conn.executescript(Path(SCHEMA_FILE).read_text(encoding="utf-8"))
    return conn


def find_album_id(conn: sqlite3.Connection, number, artist: str, album: str) -> int | None:
    """Look up an existing album by the same composite key the pipeline has
    always merged on: (catalog_number, artist, album)."""
    row = conn.execute(
        "SELECT id FROM albums WHERE catalog_number = ? AND artist = ? AND album = ?",
        (int(number), artist, album),
    ).fetchone()
    return row["id"] if row else None


def replace_media(conn: sqlite3.Connection, album_id: int, media: list):
    conn.execute("DELETE FROM media WHERE album_id = ?", (album_id,))
    for pos, m in enumerate(media):
        conn.execute(
            "INSERT INTO media (album_id, type, url, caption, position) VALUES (?, ?, ?, ?, ?)",
            (album_id, m["type"], m["url"], m.get("caption"), pos),
        )


def insert_album(conn: sqlite3.Connection, e: dict) -> int:
    """Insert a freshly-scraped-and-enriched entry (same dict shape as an
    albums_enriched.json record, plus e["medium_post_url"]) as a brand new
    album + its media/genre rows. Returns the new album_id."""
    spotify = e.get("spotify") or {}
    mb = e.get("musicbrainz") or {}

    cur = conn.execute(
        """
        INSERT INTO albums (
            catalog_number, artist, album, year, text, medium_post_url,
            spotify_url, spotify_embed_url, spotify_cover_art_url,
            spotify_matched_artist_name, spotify_matched_album_name,
            spotify_matched_release_year, spotify_exact_year_match,
            mb_country, mb_cover_art_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(e["number"]), e["artist"], e["album"], int(e["year"]), e["text"],
            e.get("medium_post_url"),
            spotify.get("spotify_url"), spotify.get("spotify_embed_url"),
            spotify.get("cover_art_url"), spotify.get("matched_artist_name"),
            spotify.get("matched_album_name"), spotify.get("matched_release_year"),
            None if "exact_year_match" not in spotify else int(spotify["exact_year_match"]),
            mb.get("country"), mb.get("cover_art_archive_url"),
        ),
    )
    album_id = cur.lastrowid
    replace_media(conn, album_id, e.get("media") or [])
    for tag in mb.get("genres") or []:
        conn.execute("INSERT INTO genres (album_id, tag) VALUES (?, ?)", (album_id, tag))
    return album_id


def update_album_text_media(conn: sqlite3.Connection, album_id: int, e: dict):
    """Refresh an existing album's header/text/media/source-url from a fresh
    scrape, WITHOUT touching its already-fetched spotify/musicbrainz data —
    the same incremental-merge behavior the JSON-based pipeline has always
    had."""
    conn.execute(
        """
        UPDATE albums SET artist=?, album=?, year=?, text=?, medium_post_url=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (e["artist"], e["album"], int(e["year"]), e["text"], e.get("medium_post_url"), album_id),
    )
    replace_media(conn, album_id, e.get("media") or [])


def mark_post_processed(conn: sqlite3.Connection, url: str):
    conn.execute(
        """
        INSERT INTO medium_posts (url, status, last_scraped_at, error_message)
        VALUES (?, 'processed', CURRENT_TIMESTAMP, NULL)
        ON CONFLICT(url) DO UPDATE SET
            status='processed', last_scraped_at=CURRENT_TIMESTAMP, error_message=NULL
        """,
        (url,),
    )


def mark_post_failed(conn: sqlite3.Connection, url: str, error: str):
    conn.execute(
        """
        INSERT INTO medium_posts (url, status, last_scraped_at, error_message)
        VALUES (?, 'failed', CURRENT_TIMESTAMP, ?)
        ON CONFLICT(url) DO UPDATE SET
            status='failed', last_scraped_at=CURRENT_TIMESTAMP, error_message=excluded.error_message
        """,
        (url, error),
    )


def export_from_db(conn: sqlite3.Connection | None = None) -> list:
    """Reconstruct the albums_enriched.json shape from the DB — the pipeline
    uses this to refresh the static snapshot the current frontend fetches;
    a future admin API route reuses it to serve the same data live."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()

    albums = conn.execute(
        "SELECT * FROM albums ORDER BY catalog_number, id"
    ).fetchall()
    media_rows = conn.execute("SELECT * FROM media ORDER BY album_id, position").fetchall()
    genre_rows = conn.execute("SELECT * FROM genres ORDER BY album_id").fetchall()

    if owns_conn:
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
