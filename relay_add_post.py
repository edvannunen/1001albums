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


def relay_add_post(url: str) -> dict:
    """Fetch `url` from wherever this process is running (expected to be a
    normal residential connection, not the prod VPS) and hand the result
    to production's /admin/add-post-relay endpoint. Returns the same stats
    dict sync_posts()/sync_prefetched_post() return. Shared by main() (CLI
    use) and server.py's /admin/relay-to-prod (a button on the *local* dev
    admin page — see server.py — so the same relay can be triggered from
    the browser instead of a terminal).

    Raises on an HTTP-level failure talking to production itself (auth,
    network, 5xx); a scrape/parse failure on production's side instead
    comes back as a normal 200 with `failed_urls` populated, same as
    /admin/add-post already does.
    """
    state = fetch_medium_post_state(url)
    resp = requests.post(
        PROD_RELAY_URL,
        data={"url": url, "state": json.dumps(state)},
        auth=(
            os.environ.get("ADMIN_USERNAME", "admin"),
            os.environ["ADMIN_PASSWORD"],
        ),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


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
