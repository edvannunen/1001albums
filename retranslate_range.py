"""
One-off / repeatable tool: force-retranslate a catalog-number range's Dutch
review text + media captions via translate.py's translate_album_content(),
overwriting whatever text_en/caption_en is already there — unlike
translate_missing(), which only fills in rows where it's NULL. Built for the
Claude -> Gemini migration (see translate.py's docstring): Ed wants the back
catalog re-translated with Gemini's noticeably better output, done in
catalog-number chunks sized to the free tier's daily quota rather than all
at once.

Usage: python retranslate_range.py <min_catalog_number> [max_catalog_number]
(max is inclusive; omit it for "min and everything above").

Commits after every album (same resumable pattern as backfill_spotify.py) —
safe to re-run. A 429 (daily free-tier quota hit) stops the run cleanly with
a note on how far it got and which catalog number to resume from tomorrow,
rather than looping/retrying pointlessly.

#500-521 were translated by hand and must never be swept up by a broad
range here — pass explicit sub-ranges (e.g. 0-499, then 522+) rather than
one 0-648 call, if that's ever the intent.
"""

import sys
import time

from dotenv import load_dotenv
import requests

from db import get_connection
from translate import translate_album_content, REQUEST_DELAY


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        print("Usage: python retranslate_range.py <min_catalog_number> [max_catalog_number]")
        return
    min_num = int(sys.argv[1])
    max_num = int(sys.argv[2]) if len(sys.argv) > 2 else None

    conn = get_connection()
    query = "SELECT id, catalog_number, artist, album, text FROM albums WHERE catalog_number >= ?"
    params = [min_num]
    if max_num is not None:
        query += " AND catalog_number <= ?"
        params.append(max_num)
    query += " ORDER BY catalog_number"
    rows = conn.execute(query, params).fetchall()

    label = f"{min_num}-{max_num}" if max_num is not None else f"{min_num}+"
    print(f"{len(rows)} albums to retranslate ({label})")

    done = 0
    for i, r in enumerate(rows):
        media_rows = conn.execute(
            "SELECT id, caption FROM media WHERE album_id = ? ORDER BY position", (r["id"],)
        ).fetchall()
        caption_rows = [m for m in media_rows if m["caption"] is not None]
        captions = [m["caption"] for m in caption_rows]

        try:
            text_en, captions_en = translate_album_content(r["artist"], r["album"], r["text"], captions)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print(
                    f"\nHit daily free-tier quota after {done}/{len(rows)} albums this run. "
                    f"Resume tomorrow with: python retranslate_range.py {r['catalog_number']}"
                    + (f" {max_num}" if max_num is not None else "")
                )
                break
            raise

        conn.execute("UPDATE albums SET text_en = ? WHERE id = ?", (text_en, r["id"]))
        for m, c_en in zip(caption_rows, captions_en):
            conn.execute("UPDATE media SET caption_en = ? WHERE id = ?", (c_en, m["id"]))
        conn.commit()
        done += 1

        print(f"  [{i + 1}/{len(rows)}] #{r['catalog_number']} {r['artist']} - {r['album']} done")
        if i < len(rows) - 1:
            time.sleep(REQUEST_DELAY)

    print(f"\nDone: {done}/{len(rows)} retranslated this run.")


if __name__ == "__main__":
    main()
