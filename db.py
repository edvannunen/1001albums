"""
Shared SQLite helpers for the 1001 Albums pipeline and (future) admin
backend. Schema lives in schema.sql; this module is the only place that
knows how to translate between a DB row and the albums_enriched.json
entry shape, so the pipeline, the migration script, and any future API
route all stay consistent.
"""

import sqlite3
from pathlib import Path

# Lives under data/, not the project root — in Coolify a persistent volume
# is mounted at /app/data (same convention as the De Sprong project), so the
# DB survives redeploys instead of being wiped along with the rest of the
# container's filesystem. schema.sql itself stays in the repo root: it's
# static reference data rebuilt fresh from git on every deploy, not runtime
# state.
DB_FILE = "data/1001albums.db"
SCHEMA_FILE = "schema.sql"


def _ensure_translation_columns(conn: sqlite3.Connection):
    """Self-healing migration for the text_en/caption_en columns, added after
    the DB (local or prod) already existed. Runs on every connection — cheap
    (a PRAGMA + no-op if already present) and means neither dev machine nor
    the Coolify persistent volume needs a manual migration step; the next
    deploy just picks it up on its first request."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(albums)")}
    if "text_en" not in cols:
        conn.execute("ALTER TABLE albums ADD COLUMN text_en TEXT")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(media)")}
    if "caption_en" not in cols:
        conn.execute("ALTER TABLE media ADD COLUMN caption_en TEXT")
    conn.commit()


def get_connection() -> sqlite3.Connection:
    """Open the DB, creating the data/ dir and schema on first use."""
    Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    is_new = not Path(DB_FILE).exists()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if is_new:
        conn.executescript(Path(SCHEMA_FILE).read_text(encoding="utf-8"))
    _ensure_translation_columns(conn)
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
            catalog_number, artist, album, year, text, text_en, medium_post_url,
            spotify_url, spotify_embed_url, spotify_cover_art_url,
            spotify_matched_artist_name, spotify_matched_album_name,
            spotify_matched_release_year, spotify_exact_year_match,
            mb_country, mb_cover_art_url
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def update_album_spotify(conn: sqlite3.Connection, album_id: int, spotify: dict):
    """Backfill a previously-unmatched album's Spotify columns. Used by the
    one-off gap backfill, not the main incremental sync (which sets these at
    insert_album time for genuinely new albums)."""
    conn.execute(
        """
        UPDATE albums SET
            spotify_url=?, spotify_embed_url=?, spotify_cover_art_url=?,
            spotify_matched_artist_name=?, spotify_matched_album_name=?,
            spotify_matched_release_year=?, spotify_exact_year_match=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            spotify.get("spotify_url"), spotify.get("spotify_embed_url"),
            spotify.get("cover_art_url"), spotify.get("matched_artist_name"),
            spotify.get("matched_album_name"), spotify.get("matched_release_year"),
            int(spotify["exact_year_match"]), album_id,
        ),
    )


def update_album_musicbrainz(conn: sqlite3.Connection, album_id: int, mb: dict):
    """Backfill a previously-unmatched album's MusicBrainz columns (country,
    cover art fallback, genre tags). Used by the one-off gap backfill, not
    the main incremental sync (which sets these at insert_album time for
    genuinely new albums)."""
    conn.execute(
        "UPDATE albums SET mb_country=?, mb_cover_art_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (mb.get("country"), mb.get("cover_art_archive_url"), album_id),
    )
    conn.execute("DELETE FROM genres WHERE album_id=?", (album_id,))
    for tag in mb.get("genres") or []:
        conn.execute("INSERT INTO genres (album_id, tag) VALUES (?, ?)", (album_id, tag))


def update_album_text_media(conn: sqlite3.Connection, album_id: int, e: dict):
    """Refresh an existing album's header/text/media/source-url from a fresh
    scrape, WITHOUT touching its already-fetched spotify/musicbrainz data —
    the same incremental-merge behavior the JSON-based pipeline has always
    had.

    Only nulls text_en when the Dutch text actually changed — the pipeline
    already re-scrapes and re-writes every album's text/media on every full
    run, and unconditionally invalidating text_en here would re-translate
    every review on every run for nothing. replace_media() below already
    fully deletes+reinserts media rows regardless, so caption_en resets to
    NULL on every call — accepted as a small, cheap re-translation cost.

    If the fresh scrape now finds a Medium-embedded Spotify link where it
    didn't before (e.g. the iframe-resolution fix picking up an older
    post's embed retroactively — see CLAUDE.md), null out the album's own
    spotify_embed_url: it's the enrichment stage's fallback search match,
    and enrich_with_spotify() itself skips that search whenever media
    already has a Spotify embed, so this keeps update-time behavior
    matching insert-time behavior and avoids rendering both. spotify_url/
    spotify_cover_art_url are left alone — cover_art_url is still used as
    album art regardless of which Spotify link is authoritative."""
    row = conn.execute("SELECT text FROM albums WHERE id=?", (album_id,)).fetchone()
    text_changed = row["text"] != e["text"]
    has_new_spotify_media = any(m["type"] == "spotify" for m in e.get("media") or [])
    conn.execute(
        """
        UPDATE albums SET artist=?, album=?, year=?, text=?, medium_post_url=?,
            text_en = CASE WHEN ? THEN NULL ELSE text_en END,
            spotify_embed_url = CASE WHEN ? THEN NULL ELSE spotify_embed_url END,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (e["artist"], e["album"], int(e["year"]), e["text"], e.get("medium_post_url"),
         text_changed, has_new_spotify_media, album_id),
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
            {"type": m["type"], "url": m["url"], "caption": m["caption"],
             "caption_en": m["caption_en"]}
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
            "text_en": a["text_en"],
            "media": media_by_album.get(a["id"], []),
            "musicbrainz": musicbrainz,
            "spotify": spotify,
        })
    return result
