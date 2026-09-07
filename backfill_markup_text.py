"""
One-off backfill: re-scrape the posts covering catalog numbers >= 515 so
their review text picks up the italics/links Medium's own markups array
carries (see enrich_1001_albums.py's apply_markups(), added alongside the
site's new rich-text edit-in-place feature) — previously silently discarded
on every scrape. Scoped to #515 onward per Ed; older albums are not
retroactively touched here.

Posts don't split exactly at #515 — the post containing #515 (509-514, "59",
"double-dutch...") also covers a few albums below it, which get refreshed as
an unavoidable side effect of re-scraping the whole post. Harmless: their
text is unchanged unless it too had markups, in which case picking them up
is a bonus, not a bug.

Reuses update_album_text_media() (the pipeline's own incremental-refresh
helper) so this behaves exactly like a normal incremental pipeline re-run
against just these posts — spotify/musicbrainz data is left untouched, and
text_en is nulled only where the (now HTML-ified) text actually changed vs
what's stored, exactly like any other re-scrape. Commits after every album,
same resumable pattern as backfill_spotify.py — safe to re-run; already-
matching text is a safe no-op each time.

translate_missing() runs once at the end to refill every text_en (and
caption_en, reset by replace_media() inside update_album_text_media) that
got nulled along the way.
"""

import anthropic

from db import get_connection, find_album_id, update_album_text_media, export_from_db
from enrich_1001_albums import fetch_medium_post_state, parse_medium_post, OUTPUT_FILE
from translate import translate_missing
import json
from pathlib import Path

MIN_CATALOG_NUMBER = 515

# Posts covering catalog numbers 510-648. #75-78 (622-648) were added via
# /admin/add-post after medium_post_urls.txt was last compiled, so they
# aren't in that file at all — pulled instead from their own medium_post_url
# already stored on the albums they produced. #59 "double-dutch" (510-516)
# is the oldest post that can contain #515.
POST_URLS = [
    "https://edvannunen.medium.com/streetwise-1001-albums-78-642-648-1989-265ae18171ca",
    "https://edvannunen.medium.com/queen-1001-albums-77-635-641-1988-89-890269565990?sharedUserId=edvannunen",
    "https://medium.com/@edvannunen/epische-proporties-1001-albums-76-628-634-1988-ac37d40a3057?sharedUserId=edvannunen",
    "https://edvannunen.medium.com/cabaret-1001-albums-75-622-627-1988-37a888f45bd5?sharedUserId=edvannunen",
    "https://edvannunen.medium.com/de-snob-1001-albums-74-615-621-1988-9c8f918291f9",
    "https://edvannunen.medium.com/bierkelder-1001-albums-73-609-614-1987-88-bf0c980a005e",
    "https://edvannunen.medium.com/krochten-1001-albums-72-602-608-1987-b432dd405b92",
    "https://edvannunen.medium.com/de-dood-van-de-engel-1001-albums-71-595-601-1987-babbb1bf4323",
    "https://edvannunen.medium.com/de-donkere-steeg-1001-albums-70-588-594-1987-59f681bcb98e",
    "https://edvannunen.medium.com/the-shrug-1001-albums-69-581-586-1986-87-cb6c612c9b35",
    "https://edvannunen.medium.com/lieve-god-1001-albums-68-574-580-1986-25a3fe091cbb",
    "https://edvannunen.medium.com/the-big-four-1001-albums-67-567-573-1986-7a6d1d4ee18c",
    "https://edvannunen.medium.com/the-godfather-1001-albums-66-560-666-1985-86-2215ab16f41d",
    "https://edvannunen.medium.com/wanneer-is-nu-1001-albums-65-553-559-1985-179e39484c74",
    "https://edvannunen.medium.com/neon-zweet-1001-albums-64-546-552-1984-85-28cf65311be5",
    "https://edvannunen.medium.com/corona-1001-albums-63-538-545-1984-b711f75192cc",
    "https://edvannunen.medium.com/stuntskischansshow-1001-albums-62-530-537-1983-84-f5fd376ec450",
    "https://edvannunen.medium.com/ratata-1001-albums-61-523-529-1983-e37ffe392d6a",
    "https://edvannunen.medium.com/lajeninaja-1001-albums-60-517-522-1983-7570cd632799",
    "https://edvannunen.medium.com/double-dutch-1001-albums-59-510-516-1982-6f92f1c20158",
]


def main():
    conn = get_connection()
    touched = 0
    skipped_not_found = 0
    skipped_below_min = 0

    for url in POST_URLS:
        print(f"Scraping {url} ...")
        state = fetch_medium_post_state(url)
        entries = parse_medium_post(state)
        print(f"  -> {len(entries)} entries")

        for e in entries:
            if float(e["number"]) < MIN_CATALOG_NUMBER:
                skipped_below_min += 1
                continue

            album_id = find_album_id(conn, e["number"], e["artist"], e["album"])
            if album_id is None:
                print(f"  !! not found in DB, skipping: #{e['number']} {e['artist']} - {e['album']}")
                skipped_not_found += 1
                continue

            e["medium_post_url"] = url
            update_album_text_media(conn, album_id, e)
            conn.commit()
            touched += 1
            print(f"  [{touched}] #{e['number']} {e['artist']} - {e['album']}")

    print(f"\n{touched} albums refreshed, {skipped_below_min} skipped (< #{MIN_CATALOG_NUMBER}), "
          f"{skipped_not_found} not found in DB.")

    print("\nRetranslating anything text_en/caption_en just nulled...")
    translate_missing(conn, anthropic.Anthropic())

    albums_out = export_from_db(conn)
    Path(OUTPUT_FILE).write_text(json.dumps(albums_out, indent=2, ensure_ascii=False), encoding="utf-8")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
