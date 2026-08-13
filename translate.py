"""
Dutch -> English translation for album review text and media captions, via
the Claude API. Shared between the one-off backfill (translate_content.py)
and the incremental pipeline (enrich_1001_albums.py's sync_posts(), so every
future post gets translated automatically).

Never touches artist/album names (passed in only as context so the model
doesn't "helpfully" translate a title), and never touches genres/countries
(those are already English and out of scope entirely).
"""

import sqlite3

import anthropic

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
You translate Dutch album review text into natural, idiomatic English for a \
personal music blog. Preserve the author's informal, personal voice, tone, \
and paragraph breaks as closely as natural English allows.

You will be given an artist name and album title purely as context. NEVER \
translate, alter, or "correct" the artist name or album title, even where \
they appear inline within the review text or captions — reproduce them \
character-for-character exactly as given.

Respond only via the tool call. Do not add commentary, notes, or anything \
not present in the source text."""

TRANSLATE_TOOL = {
    "name": "submit_translation",
    "description": "Submit the English translation of the review text and captions.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "text_en": {"type": "string", "description": "English translation of the review text."},
            "captions_en": {
                "type": "array",
                "items": {"type": "string"},
                "description": "English translation of each caption, in the same order as given.",
            },
        },
        "required": ["text_en", "captions_en"],
        "additionalProperties": False,
    },
}


def translate_album_content(
    client: anthropic.Anthropic, artist: str, album: str, text: str, captions: list[str]
) -> tuple[str, list[str]]:
    """One Claude API call per album: translates the review text and every
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

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[TRANSLATE_TOOL],
        tool_choice={"type": "tool", "name": "submit_translation"},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_use = next(b for b in response.content if b.type == "tool_use")
    text_en = tool_use.input["text_en"]
    captions_en = tool_use.input["captions_en"]
    if len(captions_en) != len(captions):
        raise ValueError(
            f"Expected {len(captions)} translated captions for {artist} - {album}, got {len(captions_en)}"
        )
    return text_en, captions_en


def translate_missing(conn: sqlite3.Connection, client: anthropic.Anthropic, verbose: bool = True):
    """Translate every album/caption still missing its English text. Safe to
    interrupt/resume — commits after every album, same reasoning as
    backfill_spotify.py."""
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

        text_en, captions_en = translate_album_content(
            client, a["artist"], a["album"], a["text"], captions
        )

        conn.execute("UPDATE albums SET text_en = ? WHERE id = ?", (text_en, a["id"]))
        for m, caption_en in zip(caption_rows, captions_en):
            conn.execute("UPDATE media SET caption_en = ? WHERE id = ?", (caption_en, m["id"]))
        conn.commit()

        if verbose:
            print(f"  [{i}/{len(albums)}] #{a['catalog_number']} {a['artist']} - {a['album']}")
