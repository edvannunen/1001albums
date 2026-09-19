"""
Dutch -> English translation for album review text and media captions, via
the Gemini API (free tier). Shared between the one-off backfill
(translate_content.py) and the incremental pipeline
(enrich_1001_albums.py's sync_posts(), so every future post gets translated
automatically).

Never touches artist/album names (passed in only as context so the model
doesn't "helpfully" translate a title), and never touches genres/countries
(those are already English and out of scope entirely).

Switched from Claude to Gemini 2026-09-19 — Ed found Gemini's translations
noticeably more natural for Dutch->English than Claude's, and Gemini Flash
is free (Google AI Studio API key, no billing needed) at this volume. Plain
REST via `requests` rather than the `google-genai` SDK, matching how the rest
of this pipeline already talks to Spotify/MusicBrainz — one fewer dependency.
`gemini-2.5-flash` (the model this was originally wired up against) 404s with
"no longer available to new users" for any key created after some point in
2026 — Google's error message itself points at `gemini-3.6-flash` as the
replacement, confirmed working via `GET .../v1beta/models` (which still
lists `gemini-2.5-flash` as if usable — don't trust that listing over an
actual generateContent call). Re-check this if it 404s again; Google's Flash
naming has moved fast (2.5 -> 3.5 -> 3.6 -> 3.7 -> 3.8 in under a year).

Moved again, same day, from `gemini-3.6-flash` to `gemini-3.1-flash-lite` —
the full-size Flash model's free tier turned out to be capped at just 20
requests/day per project (confirmed via the actual 429 body:
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, quotaValue 20), which
is far too low for retranslating hundreds of already-imported reviews in
any reasonable timeframe. The lite variant has its own, much higher daily
quota (not confirmed exactly, but didn't run out translating a full
118-album batch in one sitting) — and a side-by-side comparison across 9
albums (Claude vs. gemini-3.6-flash vs. gemini-3.1-flash-lite) showed lite
holding up fine on quality, including correctly resolving a Dutch nickname
("De Hoek") to U2 guitarist The Edge's real name, same as the full model —
Ed's own call after reading all three. If quality ever regresses noticeably
on lite, `gemini-3.6-flash` is the fallback, just budget for its 20/day cap.
"""

import json
import os
import sqlite3
import time

import requests

MODEL = "gemini-3.1-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# No published free-tier RPM figure for gemini-3.1-flash-lite as of this
# writing (Google's docs point at the AI Studio dashboard, which needs a
# logged-in session to read) — 4.5s/request (~13 RPM) is a conservative guess
# to avoid 429s, same defensive-delay approach as MB_REQUEST_DELAY in
# enrich_1001_albums.py. Only matters for translate_content.py's bulk
# backfill; the incremental per-post path translates a handful of albums at
# a time and would rarely notice either way.
REQUEST_DELAY = 4.5

SYSTEM_PROMPT = """\
You translate Dutch album review text into natural, idiomatic English for a \
personal music blog. Preserve the author's informal, personal voice, tone, \
and paragraph breaks as closely as natural English allows.

You will be given an artist name and album title purely as context. NEVER \
translate, alter, or "correct" the artist name or album title, even where \
they appear inline within the review text or captions — reproduce them \
character-for-character exactly as given.

The text may contain simple HTML formatting tags (<i>, <b>, <a href="...">, \
<blockquote>) around parts of it, e.g. an italicized song title, a link, or a \
quoted lyric. Preserve these exactly: keep each tag wrapped around the \
translated version of whatever text it originally covered, and never add, \
remove, or alter a tag or its href value. For a <blockquote> quoting song \
lyrics or a quotation originally in English, reproduce that quoted text \
unchanged rather than translating it.

Respond only with the JSON object described by the response schema. Do not \
add commentary, notes, or anything not present in the source text."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "text_en": {"type": "STRING", "description": "English translation of the review text."},
        "captions_en": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "English translation of each caption, in the same order as given.",
        },
    },
    "required": ["text_en", "captions_en"],
}



# Transient 503 ("Service Unavailable", model overloaded) is common enough on
# the free tier to hit mid-batch (confirmed: killed a 119-album retranslation
# run stone dead at album #101 with an unhandled exception) — retried with
# backoff below, same reasoning as MusicBrainz's _mb_get() in
# enrich_1001_albums.py. A 429 (daily free-tier quota) is NOT retried here —
# that's a quota exhaustion, not a transient blip, and callers (e.g.
# retranslate_range.py) handle it by stopping the whole run cleanly instead.
MAX_RETRIES = 4
RETRY_BACKOFF = 5


def translate_album_content(artist: str, album: str, text: str, captions: list[str]) -> tuple[str, list[str]]:
    """One Gemini API call per album: translates the review text and every
    caption together. Returns (text_en, captions_en) — captions_en is the
    same length/order as the input `captions` list."""
    captions_block = (
        "\n".join(f"{i}. {c}" for i, c in enumerate(captions)) if captions else "(none)"
    )
    user_message = (
        f"Artist: {artist}\nAlbum: {album}\n\n"
        f"Review text:\n{text}\n\n"
        f"Captions:\n{captions_block}"
    )

    for attempt in range(MAX_RETRIES + 1):
        response = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_message}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": RESPONSE_SCHEMA,
                },
            },
            timeout=60,
        )
        if response.status_code == 503 and attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * (attempt + 1))
            continue
        break
    response.raise_for_status()
    data = response.json()
    result = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    text_en = result["text_en"]
    captions_en = result["captions_en"]
    if len(captions_en) != len(captions):
        raise ValueError(
            f"Expected {len(captions)} translated captions for {artist} - {album}, got {len(captions_en)}"
        )
    return text_en, captions_en


def translate_missing(conn: sqlite3.Connection, verbose: bool = True, on_progress=None):
    """Translate every album/caption still missing its English text. Safe to
    interrupt/resume — commits after every album, same reasoning as
    backfill_spotify.py. `on_progress`, if given, is called with the same
    per-album line as the verbose print (see enrich_1001_albums.py's
    sync_scraped_entries) — used by the admin relay's SSE progress stream."""
    albums = conn.execute(
        """
        SELECT id, catalog_number, artist, album, text FROM albums
        WHERE text_en IS NULL
           OR id IN (SELECT album_id FROM media WHERE caption IS NOT NULL AND caption_en IS NULL)
        ORDER BY catalog_number
        """
    ).fetchall()

    for i, a in enumerate(albums, 1):
        media_rows = conn.execute(
            "SELECT id, caption FROM media WHERE album_id = ? ORDER BY position", (a["id"],)
        ).fetchall()
        caption_rows = [m for m in media_rows if m["caption"] is not None]
        captions = [m["caption"] for m in caption_rows]

        text_en, captions_en = translate_album_content(a["artist"], a["album"], a["text"], captions)

        conn.execute("UPDATE albums SET text_en = ? WHERE id = ?", (text_en, a["id"]))
        for m, caption_en in zip(caption_rows, captions_en):
            conn.execute("UPDATE media SET caption_en = ? WHERE id = ?", (caption_en, m["id"]))
        conn.commit()

        line = f"  [{i}/{len(albums)}] #{a['catalog_number']} {a['artist']} - {a['album']}"
        if verbose:
            print(line)
        if on_progress:
            on_progress(line)

        if i < len(albums):
            time.sleep(REQUEST_DELAY)
