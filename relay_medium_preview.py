"""
Local fallback for the Signal tab's post preview (share_export.html ->
/admin/medium-post-preview) when Medium's Cloudflare bot-check is
challenging requests from the Coolify VPS's own (datacenter) IP — same
intermittent issue documented for /admin/add-post in CLAUDE.md, now also
confirmed hitting this route (2026-09-07).

Unlike /admin/add-post, this route makes no database write — it's a pure
read/compute step for display in Ed's own browser, so there's no need to
relay anything back to production at all. Just fetch and extract locally
(same fetch_medium_post_state()/extract_post_preview() the server route
uses) and print the result straight to the terminal for manual copy-paste
into Signal.

Usage: python relay_medium_preview.py <medium-post-url>
"""

import sys

from enrich_1001_albums import fetch_medium_post_state, extract_post_preview


def main():
    if len(sys.argv) != 2:
        print("Usage: python relay_medium_preview.py <medium-post-url>")
        sys.exit(1)
    url = sys.argv[1]

    print(f"Fetching {url} locally ...")
    state = fetch_medium_post_state(url)
    preview = extract_post_preview(state)

    print("\n--- Top image ---")
    print(preview["image_url"] or "(none found)")

    print("\n--- Post text (paste into Signal) ---")
    print(f"{preview['title']}\n\n{preview['intro_text']}\n\n{url}")


if __name__ == "__main__":
    main()
