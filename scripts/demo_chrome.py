"""Put a demo page in the house look: the shared stylesheet, a header back to the portfolio, and a footer.

    python scripts/demo_chrome.py docs/index.html [more pages] --repo <repo-name> [--drop-styles]

Safe to run twice. --drop-styles removes the page's own <style> blocks and font links first,
for pages whose layout the shared stylesheet fully covers.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

KIT = "https://pranavgudise.dev/kit/demo.css"
SITE = "https://pranavgudise.dev/"
MARK = (
    '<svg class="demo-mark" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.25"/>'
    '<g class="n"><path class="f" d="M15.5 8.5 L 13.4 13.4 L 8.5 15.5 L 10.6 10.6 Z"/></g>'
    '<circle class="p" cx="12" cy="12" r=".7"/></svg>'
)


def chrome(page: str, repo: str, drop_styles: bool = False) -> str:
    page = re.sub(r"<!-- demo-chrome -->.*?<!-- /demo-chrome -->\n?", "", page, flags=re.S)
    if drop_styles:
        page = re.sub(r"<style[^>]*>.*?</style>\n?", "", page, flags=re.S | re.I)
        page = re.sub(r"<link[^>]+(fonts\.googleapis|fonts\.gstatic|rsms\.me)[^>]*>\n?", "", page, flags=re.I)
    head = (
        '<!-- demo-chrome --><meta name="theme-color" content="#f4f2ec">'
        f'<link rel="icon" href="{SITE}favicon.svg" type="image/svg+xml">'
        f'<link rel="stylesheet" href="{KIT}"><!-- /demo-chrome -->\n'
    )
    # The kit goes first in <head>, so a page's own styles can still refine it.
    page = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + head, page, count=1, flags=re.I)
    top = (
        f'<!-- demo-chrome --><header class="demo-top"><a class="demo-brand" href="{SITE}">{MARK}'
        f'<span>Pranav Gudise</span></a><a class="pill" href="https://github.com/pg2342-hub/{repo}">'
        "Code on GitHub</a></header><!-- /demo-chrome -->\n"
    )
    page = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + "\n" + top, page, count=1, flags=re.I)
    foot = (
        f'<!-- demo-chrome --><footer class="demo-foot"><span>A working build by '
        f'<a href="{SITE}">Pranav Gudise</a>. Synthetic or public data only.</span>'
        f'<a href="https://github.com/pg2342-hub/{repo}">github.com/pg2342-hub/{repo}</a></footer>'
        "<!-- /demo-chrome -->\n"
    )
    return re.sub(r"(</body>)", lambda m: foot + m.group(1), page, count=1, flags=re.I)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="+")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--drop-styles", action="store_true")
    args = ap.parse_args()
    for p in args.pages:
        path = Path(p)
        path.write_text(chrome(path.read_text(encoding="utf-8"), args.repo, args.drop_styles), encoding="utf-8")
