"""
Local relay for adding a new Medium post to production when Medium's
Cloudflare bot-check is challenging requests from the Coolify VPS's own
(datacenter) IP — confirmed 2026-08-18, see CLAUDE.md "Known open items".
Cloudflare's bot-fight scoring isn't a fixed block; it can start
challenging a datacenter ASN's requests at any time, and a plain
server-side `requests.get()` can never pass that challenge (it's a JS
challenge, not a header check) no matter the User-Agent.

Run this from a normal home/residential connection instead: it fetches
the post itself (same fetch_medium_post_state() the server would have
used) and hands the already-fetched Apollo state to production's
/admin/add-post-relay endpoint, which does everything else — merge into
the DB, Spotify/MusicBrainz enrichment for genuinely new albums,
translation — exactly like /admin/add-post would if it could reach Medium
directly.

Usage: python relay_add_post.py <medium-post-url>
Reads ADMIN_USERNAME/ADMIN_PASSWORD from .env (same credentials as the
/admin web login) for the request's HTTP Basic auth.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

from enrich_1001_albums import fetch_medium_post_state

load_dotenv()

PROD_RELAY_URL = "https://bier-en-brood.nl/1001albums/admin/add-post-relay"


def _prod_auth():
    return (os.environ.get("ADMIN_USERNAME", "admin"), os.environ["ADMIN_PASSWORD"])


def relay_add_post_stream(url: str):
    """Generator version of relay_add_post(): fetches `url` locally, then
    streams production's Server-Sent Events response (/admin/add-post-relay
    — see server.py) line by line, yielding each progress string as it
    arrives. The final yielded item is the stats dict instead of a string —
    callers should check `isinstance(item, dict)` to detect the end.

    Exists because a genuinely-new post's enrichment (Spotify + rate-limited
    MusicBrainz + translation) can take 60-90s+, and a single blocking POST
    made that whole stretch look identical to a hang, with no way to tell
    the two apart — confirmed 2026-08-18 (see CLAUDE.md), a request that
    was actually still succeeding server-side got reported as failed
    because the client gave up first. Streaming means progress is visible
    the whole time, and the per-chunk read timeout below only needs to
    cover the gap *between* messages, not the whole run.

    Raises on an HTTP-level failure talking to production (auth, network,
    5xx, or an `error` event from production's own worker thread); a
    scrape/parse failure on production's side instead comes back inside the
    final stats dict's `failed_urls`, same as the non-streaming path always
    did.
    """
    yield f"Fetching {url} locally..."
    state = fetch_medium_post_state(url)
    yield "Fetched. Sending to production..."

    resp = requests.post(
        PROD_RELAY_URL,
        data={"url": url, "state": json.dumps(state)},
        auth=_prod_auth(),
        stream=True,
        # (connect timeout, per-chunk read timeout) — NOT a total-duration
        # cap, since the connection stays open and SSE bytes keep arriving;
        # 120s only needs to cover the longest gap between two progress
        # messages (MusicBrainz's ~1req/sec rate limit is the slowest).
        timeout=(10, 120),
    )
    resp.raise_for_status()

    event = "message"
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            event = "message"
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data = line[len("data:"):].strip()
            if event == "done":
                yield json.loads(data)
                return
            if event == "error":
                raise RuntimeError(json.loads(data))
            yield data


def relay_add_post(url: str) -> dict:
    """Blocking wrapper around relay_add_post_stream() — prints each
    progress line as it arrives (so a terminal run of this script still
    shows live output) and returns the final stats dict. Used by main()
    below and by anywhere a single final result is enough."""
    stats = None
    for item in relay_add_post_stream(url):
        if isinstance(item, dict):
            stats = item
        else:
            print(item)
    return stats


def main():
    if len(sys.argv) != 2:
        print("Usage: python relay_add_post.py <medium-post-url>")
        sys.exit(1)
    url = sys.argv[1]

    print(f"Fetching {url} locally ...")
    print("Sending to production ...")
    stats = relay_add_post(url)

    if stats["failed_urls"]:
        print(f"Failed: {stats['failed_urls']}")
    else:
        print(
            f"Done — {stats['new']} new album(s) added"
            + (" (Spotify/MusicBrainz enrichment ran for those)" if stats["new"] else "")
            + f", {stats['updated']} already existed and had their text/media refreshed."
        )


if __name__ == "__main__":
    main()
