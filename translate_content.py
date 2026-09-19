"""
One-off backfill: translate every album's review text + media captions from
Dutch to English (translate.py's translate_missing()). Safe to re-run — only
ever picks up rows still missing text_en/caption_en, same pattern as
backfill_spotify.py.
"""

from dotenv import load_dotenv

from db import get_connection
from translate import translate_missing


def main():
    load_dotenv()
    conn = get_connection()
    translate_missing(conn)
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
