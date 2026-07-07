-- 1001 Albums — SQLite schema
--
-- Mirrors the shape albums_enriched.json already had: one row per album,
-- with media and genre tags unflattened into child tables. Genre bucketing
-- (GENRE_TAXONOMY in js/taxonomy.js) stays client-side — this table stores
-- the raw MusicBrainz tags, not the classified bucket.

CREATE TABLE albums (
  id                            INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_number                INTEGER NOT NULL,
  artist                        TEXT NOT NULL,
  album                         TEXT NOT NULL,
  year                          INTEGER NOT NULL,
  text                          TEXT,
  medium_post_url               TEXT,   -- NULL for the initial migration backfill;
                                         -- populated going forward once the pipeline
                                         -- tags entries with their source post at scrape time
  spotify_url                   TEXT,
  spotify_embed_url             TEXT,
  spotify_cover_art_url         TEXT,
  spotify_matched_artist_name   TEXT,
  spotify_matched_album_name    TEXT,
  spotify_matched_release_year  INTEGER,
  spotify_exact_year_match      INTEGER,  -- 0/1, NULL if no spotify match at all
  mb_country                    TEXT,
  mb_cover_art_url               TEXT,
  created_at                    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at                    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(catalog_number, artist, album)  -- same composite key the pipeline merges on
);

CREATE TABLE media (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  album_id    INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,  -- youtube|spotify|image|other
  url         TEXT NOT NULL,
  caption     TEXT,
  position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE genres (
  album_id    INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  tag         TEXT NOT NULL
);

-- Drives the admin "add via URL" workflow: which Medium posts have already
-- been scraped, so re-submitting the same URL is a safe no-op.
CREATE TABLE medium_posts (
  url               TEXT PRIMARY KEY,
  status            TEXT NOT NULL DEFAULT 'pending',  -- pending|processed|failed
  last_scraped_at   TEXT,
  error_message     TEXT
);

CREATE INDEX idx_media_album_id ON media(album_id);
CREATE INDEX idx_genres_album_id ON genres(album_id);
CREATE INDEX idx_albums_catalog_number ON albums(catalog_number);
