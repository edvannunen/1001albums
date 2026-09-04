# 1001 Albums

## What this project is

Ed has written 621+ short album reviews on Medium ("De Snob — 1001 Albums"
series, @edvannunen), one post per ~6-7 albums, covering the book *1001
Albums You Must Hear Before You Die* (2018 edition). Each entry has a
header (`NNN Artist — Album (Year)`), a short review text, and either a
YouTube embed, a Spotify embed, or an image (older posts), used as an
illustrative caption/clip.

Goal: pull all of this out of Medium, enrich it with album art, Spotify
links, country of origin, and genre tags, and build a searchable/sortable
personal site with a small stats dashboard. Ed will keep publishing new
entries, so the pipeline should be easy to re-run incrementally.

## Current state

- `enrich_1001_albums.py` — the enrichment pipeline (4 stages, see below).
  Spotify/MusicBrainz credentials, plus `ANTHROPIC_API_KEY` for the
  translation stage, live in `.env` (see `.env.example`).
- Storage is SQLite (`data/1001albums.db`, schema in `schema.sql`, helpers
  in `db.py`) — migrated from flat-JSON 2026-07 (`migrate_json_to_sqlite.py`
  is the one-off migration script, safe to re-run but shouldn't need to be).
  `albums_enriched.json` is regenerated from the DB on every pipeline run
  (`db.export_from_db`) so the static frontend keeps working unchanged.
- `index.html` — the frontend (modular components under `js/`, no build
  step), wired to the real `albums_enriched.json` and actively developed
  (dashboard, grid/table, search/sort/filter, album modal all working
  against real data).
- `medium_post_urls.txt` — done, 77 URLs, one per "De Snob" post.
- `albums_enriched.json` — generated, 621 album entries, 619 with a
  Spotify link (2 confirmed not on Spotify at all — see
  `spotify_manual_review.md`). Re-running `enrich_1001_albums.py` is safe
  and incremental (see "Known open items").

## Deployment

Live at `https://bier-en-brood.nl/1001albums`. Coolify auto-deploys on every push to `main`
(GitHub App webhook — no manual deploy step). Server/infra details (SSH access, env vars,
start command, DB volume path, backups) live in `../Coolify Hosting Playbook.md`,
not duplicated here — check that file for anything deploy- or server-related.

## Pipeline stages (enrich_1001_albums.py)

1. **Medium scrape** — fetches the plain rendered post page and parses its
   embedded Apollo GraphQL cache (`window.__APOLLO_STATE__`; see "Known
   open items" for why this replaced the old `?format=json` trick), walks
   the paragraph tree, splits into per-album entries at each `H3`/`H4`
   header matching `NNN Artist — Album (Year)`. Captures review text (`P`
   paragraphs), and whatever media type is attached (`IMG`, or `IFRAME` for
   YouTube/Spotify — resolved via `iframe.mediaResource` into the real
   URL).

2. **Spotify enrichment** — only runs for entries that don't already have
   a Spotify embed from Medium. Searches Spotify, filters to
   `album_type == "album"`, and picks the release closest to the book's
   year — this is the heuristic for choosing the original pressing over
   a remaster/deluxe/expanded edition. Falls back to whatever's closest
   if nothing matches the year exactly. Flags non-matches so they can be
   checked by hand (a few albums won't be on Spotify at all).

3. **MusicBrainz enrichment** — artist's country of origin (via the
   artist's `area` field, not release country — this is "where the band
   is from", which is what the dashboard wants), genre tags, and a
   Cover Art Archive URL as an art fallback for the rare cases Spotify
   has nothing.

4. **Translation** (`translate.py`) — Dutch review text + media captions
   translated to English via the Claude API (Sonnet 5), so the frontend can
   offer NL/EN without a second dataset. Runs last in `sync_posts()`, only
   for albums/captions still missing `text_en`/`caption_en` — cheap and
   idempotent, same "only touch what's actually new" shape as the rest of
   the pipeline. **Never translates artist/album names** (passed to the
   model only as context, explicitly instructed not to alter them), and
   genres/countries are out of scope entirely — they're already English.
   `update_album_text_media()` only nulls `text_en` when the Dutch text
   actually changed, so a full pipeline re-run doesn't re-translate
   everything; media rows are always fully replaced on refresh (existing
   behavior), so `caption_en` resets and gets cheaply retranslated each time
   — accepted as negligible cost. One-off backfill for existing data:
   `translate_content.py` (mirrors `backfill_spotify.py`'s
   commit-per-album, safe-to-resume pattern).

Output: `albums_enriched.json`, one record per album:
```json
{
  "number": "615",
  "artist": "...",
  "album": "...",
  "year": "1988",
  "text": "...",
  "text_en": "... | null",
  "media": [
    {"type": "youtube|spotify|image|other", "url": "...", "caption": "...", "caption_en": "... | null"}
  ],
  "spotify": {
    "spotify_url": "...",
    "spotify_embed_url": "...",
    "cover_art_url": "...",
    "matched_artist_name": "...",
    "matched_album_name": "...",
    "matched_release_year": 1988,
    "exact_year_match": true
  },
  "musicbrainz": {
    "country": "United Kingdom",
    "genres": ["Sophisti-Pop", "Jazz Pop"],
    "cover_art_archive_url": "..."
  }
}
```

Tracklists are deliberately NOT fetched — the Spotify embed covers that
live on the site, no need to duplicate it in the data.

## Known open items / things to verify

- **`/admin/add-post`'s direct Medium fetch can get Cloudflare-challenged
  from production — confirmed 2026-08-18.** Not a fixed IP ban: the
  response was a real Cloudflare JS challenge (`cf-mitigated: challenge`,
  body is the "Just a moment..." page), and posts #75/#76 had genuinely
  been added successfully via the live `/admin` panel before this (their
  `medium_posts.last_scraped_at` timestamps are distinct from the original
  bulk-migration batch). Cloudflare's bot-fight scoring flags datacenter
  ASNs (Hetzner, here) on and off dynamically — the same server IP can
  work one week and get challenged the next, with no code change on our
  side. A plain `requests.get()` can never pass a JS challenge regardless
  of User-Agent. Fixed with a relay path for when this happens again:
  `relay_add_post.py`, run locally (residential IP, not challenged) —
  fetches the post itself via the existing `fetch_medium_post_state()`,
  then POSTs the already-fetched Apollo state to a new
  `/admin/add-post-relay` endpoint (`server.py`), which skips the network
  fetch and does everything else `sync_posts()` normally does (merge,
  Spotify/MusicBrainz enrichment for new albums, translation). The
  `/admin/add-post` web form is untouched and still tries the direct
  fetch first, since it's free and sometimes still works — reach for the
  relay script specifically when that form's error message shows up.
- **`?format=json` is dead — Medium/Cloudflare now ignores that query
  param entirely** (confirmed 2026-07: `CF-Cache-Status: HIT` on every
  request regardless of cache-busting params; a plain server-rendered HTML
  page comes back instead of the raw JSON payload, and the old
  `])}while(1);</x>`-prefixed split throws `IndexError` since the marker
  is no longer present). Replaced with parsing the rendered page's
  embedded Apollo GraphQL cache (`window.__APOLLO_STATE__`, extracted via
  brace-matching from the `<script>` tag — see `extract_js_object()`).
  This turned out to be a strictly better source, see below.
- **Header regex and paragraph types — verified against 3 real posts**
  (2026-era #74, ~2019-era #2, ~2021-era #20), and the original
  assumptions were wrong in ways worth remembering:
  - The Apollo cache represents paragraph `type` as a **string**
    (`H3`/`H4`/`P`/`IMG`/`IFRAME`/`MIXTAPE_EMBED`), not the integer codes
    the old `?format=json` endpoint used.
  - **Per-album headers are type `P`, same as body paragraphs** — there
    is no distinct heading type to filter on. The parser applies the
    header regex to every `P` paragraph's text instead of gating on an
    `H3`/`H4` type. The post's own title is always paragraph 0 (sometimes
    typed `H3`/`H4`, sometimes just `P` for older posts with no distinct
    heading type at all) — skipped unconditionally by index rather than
    by type, since type alone can't reliably identify it.
  - **Older posts (pre-~2020) fuse the header into the same paragraph as
    the start of the review**, e.g. `"7 Frank Sinatra – Songs for
    Swingin' Lovers (1956). Daar is Frank weer..."` — one paragraph, not
    two. The regex now captures a trailing group for this case and
    seeds the entry's `text` with it, rather than assuming the header
    paragraph is standalone.
  - **Dash separator must be en/em dash (`–`/`—`) only, not a plain
    hyphen** — artist names with hyphens (e.g. "The Go-Betweens") were
    being mis-split at the wrong dash when a plain hyphen was allowed in
    the separator character class.
  - **The number prefix isn't always followed by a plain space** — post
    #11 uses a period (`"84. The Beau Brummels – Triangle (1967)"`),
    post #10 uses a colon (`"76: Astrud Gilberto – Beach Samba (1967)"`).
    The regex allows an optional `.` or `:` before the whitespace. Given
    three variants found across a handful of posts, assume more exist;
    a suspiciously-low or zero entry count for any post is worth
    checking against the raw paragraph text before assuming the post
    genuinely has no albums.
  - Caption handling was right: the embed's own `text` field is the
    caption (confirmed on real `type: 11` paragraphs), not a separate
    paragraph.
- **Spotify search had to be redesigned — the field-filtered `artist:X
  album:Y` query syntax proved unreliable in three separate ways**,
  found by testing the full 77-post run:
  - The `limit` param is capped at 10 for this app (Development Mode,
    not yet through Extended Quota review) — anything above 10 returns
    a 400 "Invalid limit" despite the docs describing up to 50. This
    silently produced a 0% match rate the first time (every query
    failed, including obvious ones like Miles Davis' Birth of the Cool).
  - Field-filtered queries return **zero results** for names containing
    an apostrophe (e.g. "Cosmo's Factory") even when the value is
    quoted — no workaround found other than abandoning the field syntax.
  - Spotify's own metadata inconsistently **drops a leading "The"** from
    some artist names (e.g. "The Sisters of Mercy" is indexed as just
    "Sisters of Mercy"), which an exact field match can't tolerate.
  - Fixed by switching to a **plain free-text query** (`f"{artist}
    {album}"`, no field prefixes) plus client-side scoring of results by
    name similarity (`difflib.SequenceMatcher` over both artist and
    album name — already imported in the original script but never
    actually wired in, suggesting this was the intended design all
    along). Reissue-marked names ("... (Expanded Edition)", "...
    (Remastered)") are deprioritized versus a plain-titled candidate at
    the same similarity, since a reissue often carries the *original*
    release year in its own metadata and would otherwise look like an
    "exact year match" despite being the wrong pressing.
  - This does **not** fix cases where Spotify's catalog only has the
    reissue indexed at all (confirmed for The Pogues' "Rum, Sodomy and
    the Lash", which only exists on Spotify as "Rum Sodomy & The Lash
    (Expanded Edition)") — there's no better candidate to prefer. The
    result now includes `matched_artist_name` / `matched_album_name` (the
    actual Spotify title matched) specifically so these remain visible
    for manual spot-checking rather than silently trusted.
- **Spotify gap backfill, done 2026-07-07** — 243 albums (no Medium-embedded
  Spotify link) had never actually been enriched at all; a rate limit hit
  during testing had blocked every attempt and it was never revisited. The
  block had cleared by the time this was investigated.
  - **Found a real false positive while testing**: Fats Domino's "This is
    Fats" (1956) confidently matched "Fats Is Back" (1968) — same artist
    (artist_sim=1.0) masking a wrong album (album_sim=0.583) behind the
    combined average. Fixed with `ALBUM_SIM_THRESHOLD` (0.65): below this,
    `spotify_search_album()` reports `"confident": False` and callers treat
    it as no match instead of trusting a "best of 10 bad options."
  - **That floor initially over-flagged correct matches**: a lot of older
    catalog is only indexed on Spotify as the remaster/anniversary/deluxe
    pressing, and the qualifier text alone (e.g. "(Remastered)") tanked the
    similarity score for an otherwise-correct match (e.g. "War (Remastered)"
    scored 0.316 for U2's "War"). Fixed with `strip_reissue_suffix()` —
    strips a trailing `(...)` qualifier before scoring, but only if it
    actually contains a `REISSUE_MARKERS` keyword, so a genuinely different
    release (e.g. "Document (R.E.M. No. 5)") isn't masked the same way.
  - `backfill_spotify.py` (one-off, run against just the gap albums) commits
    **after every album**, unlike `enrich_with_spotify()`'s callers which
    only `insert_album()` once a whole new-entries batch finishes — a 429
    mid-run here can't lose already-confirmed matches, and re-running only
    ever re-tries whatever's still missing `spotify_url`.
  - Result: 230/243 auto-confirmed, 13 needed a manual look, resolved by
    hand — see `spotify_manual_review.md`. Final: 619/621 albums have a
    Spotify link; #5 and #137 confirmed genuinely absent from Spotify.
- **A second, distinct Spotify gap — "already has a Spotify embed" wrongly
  skipped the real album search — found and fixed 2026-09-04.**
  `enrich_with_spotify()`'s skip condition only checks whether *any*
  `type='spotify'` media row exists for the album; it doesn't check whether
  that embed is actually *from* the catalogued album. When a review is
  illustrated with a different song (a later single, a preview, a related
  track), the album silently never gets a real Spotify search at all — found
  via #648 Neneh Cherry's "Raw Like Sushi" (1989), whose only Spotify embed
  was a 2014 "Blank Project" track. Checked the scope: 26 albums had
  `spotify_url IS NULL` despite having a `spotify`-type media row (some of
  those embeds were actually the right album, just never copied into the
  dedicated `spotify_url`/cover-art columns; others were illustrative
  off-album tracks like #648). Fixed with `backfill_spotify_embedded_gap.py`
  (mirrors `backfill_spotify.py`'s commit-per-album pattern, `WHERE
  spotify_url IS NULL AND id IN (media WHERE type='spotify')` instead of
  `NOT IN`) — 18/26 auto-confirmed and applied (locally and to production,
  via direct SQL per the Coolify playbook's single-row-correction recipe,
  not a second query against prod). Of the 7 flagged for manual review, Ed
  confirmed 5 by hand the same day (title-format/reissue mismatches, not
  wrong albums — #29, #38, #95, #259, #645), also written to both DB
  copies; #19 and #233 are still genuinely open, see
  `spotify_manual_review_embedded_gap.md`. The underlying skip-condition bug
  in `enrich_with_spotify()` itself is NOT fixed — a future post whose
  embed is an off-album track will hit this again; worth a real fix
  (compare the embedded Spotify URL's matched album/artist against the
  catalogued one, not just "does an embed exist at all") if it keeps
  recurring.
- **MusicBrainz artist lookup crashed on artists with no known area** —
  `artist.area` is `null` (present, not absent) in MusicBrainz's own
  JSON for such artists, and `.get("area", {})` only falls back to `{}`
  when the key is *absent*, not when it's `None` — crashed with
  `AttributeError: 'NoneType' object has no attribute 'get'` partway
  through the first full run. Fixed with `(data.get("area") or {})`.
- **MusicBrainz enrichment was silently dropping data on rate-limit
  throttling — fixed 2026-08-22.** `REQUEST_DELAY` (0.3s) is shared with
  Spotify calls but far too fast for MusicBrainz's documented ~1 req/sec
  limit, and `musicbrainz_lookup()` treated any non-200 response
  (a 503/429 throttle, or a transient timeout) identically to a genuine
  "this album isn't in MusicBrainz" — silently returning `{}` either way,
  with no retry and no way to tell the two cases apart later. Left 48
  albums with zero country/genre data at all, including catalog entries
  that are obviously in MusicBrainz (the Beatles' *Revolver*, *Sgt.
  Pepper's*, Prince's *Sign O' the Times*). Fixed with `MB_REQUEST_DELAY`
  (1.1s) plus retry-with-backoff on 429/503 in a new `_mb_get()` helper.
  Also added a fuzzy free-text fallback query (scored by
  `difflib.SequenceMatcher`, same approach as the Spotify fix above) for
  when the strict `artist:"X" AND release:"Y"` Lucene phrase match finds
  nothing because our stored title differs slightly from MusicBrainz's own
  canonical title (e.g. Queen Latifah's "All Hail To The Queen" vs.
  MusicBrainz's "All Hail the Queen") — only accepted above a 0.75
  similarity floor so an unrelated same-ish-named album isn't wrongly
  attached. One-off backfill: `backfill_musicbrainz.py` (mirrors
  `backfill_spotify.py`'s commit-per-album, safe-to-resume pattern),
  re-run against the 48 gap albums: 43 recovered, 5 genuinely have no
  MusicBrainz entry at all (Tito Puente's "Dance Mania vol. 1", Aretha
  Franklin's "Lady Soul", Stephen Stills' "Manassas", Big Star's
  "Third/Sister Lovers", Run DMC's self-titled debut) — not yet manually
  resolved. The 43 recovered rows were applied to production directly via
  SQL generated from the local results (see "Single-row/single-field data
  corrections" in the Coolify playbook) rather than re-running the
  backfill against prod, since MusicBrainz was already queried once.
- **`mb_country` was storing a city/UK-subdivision name instead of the
  actual country for ~30 albums — fixed 2026-08-22.** `musicbrainz_lookup()`
  read the artist's raw `area.name`, but MusicBrainz's `area` is set at
  whatever granularity it happens to have on file for that artist, not
  necessarily country level — produced values like "Boston" (the band, not
  a country), "New York", "Phoenix", "Memphis", "Los Angeles", "London",
  "Ladysmith" (Ladysmith Black Mambazo), and a `England`/`Scotland`/
  `Northern Ireland`/`Wales` split alongside the correct "United Kingdom"
  for other UK artists — all found via the table view's new country-column
  sort (previous bullet), which put them all in obvious clusters for the
  first time. Fixed by preferring the artist lookup's top-level `country`
  field instead — an ISO 3166-1 alpha-2 code MusicBrainz has *already*
  resolved to true country level regardless of area granularity (confirmed
  live: Boston the band has `area.name = "Boston"` but `country = "US"`) —
  resolved to a display name via a new `resolve_country_name()` (queries
  MusicBrainz's own area-by-ISO-code search so the name matches MB's own
  convention already used elsewhere in the DB, e.g. "United Kingdom" not
  some ISO long-form string; cached module-wide since only a few dozen
  distinct countries recur across hundreds of artists). Falls back to
  `area.name` only when `area` itself already carries `iso-3166-1-codes`
  (i.e. it's already country-level) and no top-level `country` is present.
  One-off fix: `fix_musicbrainz_country.py`, re-run against every album
  whose `mb_country` was one of the known-bad city/subdivision values or
  `NULL` (36 albums) — 30 corrected, 6 unchanged (the same genuine
  MusicBrainz gaps from the previous bullet). Applied to production the
  same way: SQL generated from the local results, not a second MusicBrainz
  query pass. The frontend's `COUNTRY_ISO` map in `js/data.js` already had
  alias entries for several of these bad values (`"Boston":"US"`,
  `"England":"GB"`, `"Ladysmith":"ZA"`, etc.) compensating for this exact
  bug at the flag-icon layer — now redundant but harmless, left in place.
- **The remaining 6 genuine MusicBrainz gaps (Tito Puente, Beatles'
  *Revolver*/*Sgt. Pepper's*, Aretha Franklin, Stephen Stills, Big Star, Run
  DMC — see the two bullets above) were manually entered — fixed 2026-08-22.**
  `mb_country` set by hand (Ed provided the countries directly, no
  MusicBrainz call), but doing that alone didn't fix what the site showed:
  `db.export_from_db()` used `mb_cover_art_url IS NULL` as its sole signal
  for "no MusicBrainz match at all" (returning `{}` for the whole
  `musicbrainz` object in that case) — since these albums never had a real
  MB match, `mb_cover_art_url` stayed NULL even after `mb_country` was set,
  so the export kept silently dropping the country that had just been
  written. Not a caching or deploy issue, despite looking exactly like one
  (confirmed identical stale value from a direct SQLite query on the file,
  from inside the container via `docker exec`, and via the app's own
  internal `localhost:8000` HTTP response — ruling out Cloudflare, browser
  cache, and container/volume mismatches one at a time before finding this).
  Fixed by treating either `mb_country` or `mb_cover_art_url` being non-NULL
  as "has some data" in `export_from_db()`, rather than gating on cover art
  alone.
- **Embed source URLs (YouTube/Spotify) now resolve for every post,
  regardless of age — fixed 2026-07.** The old `?format=json` approach's
  `iframe.thumbnailUrl`/`externalSrc` only worked for recently-created
  posts. The Apollo-cache approach resolves every `IFRAME` paragraph via
  `iframe.mediaResource` (an Apollo ref) → `MediaResource.iframeSrc`, an
  embed.ly wrapper URL
  (`cdn.embedly.com/widgets/media.html?src=...&url=<real_url>&...`) whose
  own `url=` query param is the real, playable YouTube watch / Spotify
  URL — Medium's rendered page always carries this, confirmed back to the
  oldest posts (#2, 2019). See `resolve_iframe_url()`. `MIXTAPE_EMBED`
  paragraphs (Medium's link-preview cards for plain hyperlinks, e.g. a
  linked news article) resolve the same way as before, via
  `mixtapeMetadata.href`.
- **`main()`'s merge is incremental and keyed on (number, artist, album),
  not number alone.** A handful of posts reuse the same catalog number
  for two different albums (see the duplicate-number bullet below) — a
  number-only key would silently collapse those pairs into one record on
  every re-run. Existing entries keep their already-fetched
  spotify/musicbrainz data and only get text/media refreshed from a fresh
  scrape; only genuinely new (number, artist, album) triples go through
  Spotify/MusicBrainz. This implements the "next steps" incremental
  re-run item below.
- **Three posts scrape 0 album entries — confirmed genuine, not a parsing
  bug** (checked 2026-07 by dumping their raw paragraphs): the Golden
  Earring tribute post (`cut-1001-albums-golden-earring-cut-voor-george-*`,
  a one-off, non-numbered bonus post), the "De 70s van 1001 Albums en van
  mij" decade-retrospective post, and the very first "1001 Albums — intro"
  post. None follow the numbered `NNN Artist — Album (Year)` format at
  all. Don't re-investigate these on future reruns unless the count
  changes.
- **Three catalog numbers are each used twice by genuine authoring typos
  in the original posts** (not a scraper bug, confirmed against raw post
  text): `#328` (The Dictators' "Go Girl Crazy!" AND NEU!'s "NEU! '75"),
  `#289` (Mike Oldfield's "Tubular Bells" AND Elton John's "Goodbye Yellow
  Brick Road"), `#389` (Television's "Marquee Moon" AND Wire's "Pink
  Flag"). `#18`, `#287`, `#380`, `#399` never appear at all. Full details
  in `img/album typos - to be fixed.txt`. Ed is aware and plans to decide
  on renumbering later — don't "fix" this unilaterally; the pipeline
  currently preserves both entries in each pair as-published (see the
  composite merge-key bullet above).
- **`medium_post_urls.txt` — done, 77 URLs.** The profile page only
  server-renders its most recent 10 posts (confirmed via embedded
  `__APOLLO_STATE__` — Medium paginates further posts client-side via
  GraphQL on scroll); a year-based static archive
  (`edvannunen.medium.com/archive/<year>`) does not exist for this
  account (404). Full list was collected by scrolling the profile page
  to the bottom, then running a one-line `document.querySelectorAll`
  snippet in the browser console to extract clean post URLs.
- **MusicBrainz rate limit** is ~1 req/sec — budget ~10-15 min for a full
  621-album run (2 lookups per album: release-group + artist).
- **RYM was deliberately dropped** as a data source (aggressive
  anti-scraping, ToS disallows automated access) in favor of Spotify +
  MusicBrainz, both official/free APIs. Discogs API is a possible backup
  for genre/style if MusicBrainz tags are too sparse for some albums —
  not yet implemented.
- **Frontend has no build step** — `albums_enriched.json` just needs to
  sit next to `index.html`. A local server may still be needed for
  `fetch()` to work depending on browser (`python -m http.server` in the
  folder is the easy fix if opening the file directly hits CORS/file://
  issues).
- **Design direction chosen**: dark "record sleeve" palette (near-black
  background, mustard accent, teal secondary), Instrument Serif for
  display type, Space Mono for data/catalog numbers, Work Sans for body.
  Signature element is the vinyl-groove progress ring showing X/1001
  complete. If redesigning, keep the numbered-catalog motif since the
  content is a genuinely numbered sequence (matches the book's own
  numbering).

## Next steps, roughly in order

Done: `medium_post_urls.txt` built (77 URLs); scrape stage tested against
3 real posts spanning 2019-2026; full pipeline run with real
Spotify/MusicBrainz credentials; `index.html` wired to real
`albums_enriched.json` with working grid/table/search/sort/dashboard;
incremental re-run implemented (re-running `enrich_1001_albums.py` only
touches genuinely new entries, see "Known open items"); storage migrated
to SQLite; Spotify gap backfill done — 619/621 albums now have a Spotify
link (see "Known open items").

1. ~~Spot-check the Spotify year-matching~~ — done 2026-07-07 (see "Known
   open items"). MusicBrainz country/genre spot-check for obviously wrong
   matches (compilations, self-titled albums, common band names) still
   not done.
2. ~~Decide on hosting~~ — done, deployed to Coolify (see Deployment section above).
3. Decide how to handle the 3 duplicate-catalog-number typos and 4
   never-used numbers (see "Known open items" and `img/album typos - to
   be fixed.txt`) — deferred, Ed's call on renumbering.
