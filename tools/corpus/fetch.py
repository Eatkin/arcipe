"""
Fetch recipe pages into a local cache (corpus/pages/), politely.

    uv run python tools/corpus/fetch.py sandra       # every post in vegansandra.com's sitemap
    uv run python tools/corpus/fetch.py bookmarks    # vegan-ish bookmarks from ../recipe-viewer

Pages already cached are never re-fetched. One request every DELAY seconds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"
PAGES = CORPUS / "pages"
INDEX = CORPUS / "index.json"  # url -> {"file", "source", "status"}
BOOKMARKS = ROOT.parent / "recipe-viewer" / "recipes.yaml"

SANDRA_SITEMAP = "https://www.vegansandra.com/sitemap.xml"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DELAY = float(os.environ.get("FETCH_DELAY", "4"))
MAX_BACKOFF = 300

# Bookmarks that aren't tagged vegan but are vegetable-led and easy to veganise
VEGANISABLE = {
    "https://www.pinchofnom.com/recipes/cauliflower-madras/",
    "https://www.pinchofnom.com/recipes/mushy-pea-curry/",
    "https://www.pinchofnom.com/recipes/singapore-style-noodles/",
    "https://www.pinchofnom.com/recipes/chicken-chow-mein/",
    "https://www.pinchofnom.com/recipes/cajun-style-dirty-rice/",
}
VEGANISABLE_DOMAINS = {
    "ekantcookcurry.wordpress.com",
    "pen-online.com",
    "101cookbooks.com",
    "minimalistbaker.com",
    "paulhollywood.com",
    "www-bbc-co-uk.cdn.ampproject.org",
    "recipetineats.com",
}


# Bookmarks saved through a wrapper (Google AMP) -> the real page
REWRITE = {
    "https://www-bbc-co-uk.cdn.ampproject.org/v/s/www.bbc.co.uk/food/recipes/irishsodabread_67445/amp": "https://www.bbc.co.uk/food/recipes/irishsodabread_67445",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def load_index() -> dict:
    return json.loads(INDEX.read_text()) if INDEX.exists() else {}


def fetch_all(urls: list[str], source: str) -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    index = load_index()
    todo = [u for u in urls if index.get(u, {}).get("status") != "ok"]
    print(f"{source}: {len(urls)} urls, {len(todo)} to fetch")
    for n, url in enumerate(todo, 1):
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".html"
        backoff = 30.0
        while True:
            try:
                (PAGES / name).write_bytes(get(url))
                index[url] = {"file": name, "source": source, "status": "ok"}
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and backoff <= MAX_BACKOFF:
                    wait = float(e.headers.get("Retry-After") or backoff)
                    print(f"  429 — waiting {wait:.0f}s", flush=True)
                    time.sleep(wait)
                    backoff *= 2
                    continue
                index[url] = {"file": None, "source": source, "status": f"error: {e}"}
                print(f"  ! {url}: {e}", flush=True)
                break
            except (urllib.error.URLError, TimeoutError) as e:
                index[url] = {"file": None, "source": source, "status": f"error: {e}"}
                print(f"  ! {url}: {e}", flush=True)
                break
        if n % 20 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
            INDEX.write_text(json.dumps(index, indent=1))
        time.sleep(DELAY)
    INDEX.write_text(json.dumps(index, indent=1))
    ok = sum(1 for u in urls if index.get(u, {}).get("status") == "ok")
    print(f"{source}: {ok}/{len(urls)} cached")


def sandra_urls() -> list[str]:
    xml = get(SANDRA_SITEMAP).decode()
    return re.findall(r"<loc>([^<]+)</loc>", xml)


def bookmark_urls() -> list[str]:
    entries = yaml.safe_load(BOOKMARKS.read_text())
    urls = []
    for e in entries:
        url, domain = e["url"], e.get("domain", "")
        tags = [t.lower() for t in (e.get("tags") or [])]
        if "youtube.com" in domain or "vegansandra.com" in domain:
            continue  # no recipe markup / covered by the sitemap
        if "vegan" in tags or url in VEGANISABLE or domain in VEGANISABLE_DOMAINS:
            urls.append(REWRITE.get(url.split("?")[0], url))
    return urls


if __name__ == "__main__":
    which = sys.argv[1:] or ["sandra", "bookmarks"]
    if "bookmarks" in which:
        fetch_all(bookmark_urls(), "bookmarks")
    if "sandra" in which:
        fetch_all(sandra_urls(), "vegansandra")
