# Spotify manual review — resolved 2026-07-07

Backfill of the 243 albums that never got a Spotify lookup (see CLAUDE.md's
"Known open items"). 230 auto-confirmed by `backfill_spotify.py`'s
similarity-floor matcher; the remaining 13 were checked by hand and applied
directly to the DB via a one-off script — all correct/found except two.

**Confirmed not on Spotify at all** (leave `spotify_url` NULL — a future
`backfill_spotify.py` re-run will still pick these two up since there's no DB
flag for "checked, absent"; that's fine, low cost to re-confirm):
- #5 Fats Domino — This is Fats (1956)
- #137 Captain Beefheart And His Magic Band — Trout Mask Replica (1969)

Everything else in the original 13-row review list was manually confirmed
correct or replaced with the right album and written to the DB. 619/621
albums now have a Spotify link (241 via lookup, 378 via a Medium-embedded
link, 2 genuinely absent).
