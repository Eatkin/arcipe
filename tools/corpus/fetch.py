"""
Fetch recipe pages into a local cache (corpus/pages/), politely.

    uv run python tools/corpus/fetch.py sandra       # every post in vegansandra.com's sitemap
    uv run python tools/corpus/fetch.py bookmarks    # vegan-ish bookmarks from ../recipe-viewer
    uv run python tools/corpus/fetch.py pinchofnom   # picks from pinchofnom.com's index
    uv run --extra scrape python tools/corpus/fetch.py sandra --browser

Pages already cached are never re-fetched. One request every DELAY seconds.
--browser loads pages in undetected Chrome instead of urllib, for sites that
rate-limit plain clients (vegansandra.com 429s after ~100 requests).
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
# Category listings, 36 recipes a page, paged as .../page/N/
PON_CATEGORIES = (
    "https://pinchofnom.com/category/food/vegan/",
    "https://pinchofnom.com/category/food/vegetarian/",
)
PON_URLS = CORPUS / "pinchofnom-urls.json"  # the crawled listing, so it's done once
PON_PICKS = Path(__file__).with_name("pinchofnom_picks.txt")  # what to fetch
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DELAY = float(os.environ.get("FETCH_DELAY", "4"))
MAX_BACKOFF = 300
MAX_FAILS = 5
HUMAN_WAIT = float(os.environ.get("HUMAN_WAIT", "300"))  # seconds to solve a captcha

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


def chrome_major() -> int | None:
    """Major version of the installed Chrome, so the patched driver matches it."""
    import shutil
    import subprocess

    exe = shutil.which("google-chrome") or shutil.which("chromium")
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.", out)
    return int(m.group(1)) if m else None


def notify(title: str, body: str) -> None:
    """Desktop notification, if notify-send is around."""
    import shutil
    import subprocess

    if exe := shutil.which("notify-send"):
        subprocess.run([exe, "-u", "critical", title, body], check=False)


class BlockedError(Exception):
    """The browser got an error page instead of the post."""


# A string every real page from a source has and challenge or error pages don't
MARKERS = {
    "vegansandra": "post-body",  # Blogger post body
    "pinchofnom": "ld+json",  # schema.org Recipe; Cloudflare's challenge has none
}


class Browser:
    """Undetected Chrome, one tab, reused for every page."""

    def __init__(self, source: str) -> None:
        self.marker = MARKERS.get(source, "<body")
        self.blogger = source == "vegansandra"
        self.start()

    def start(self) -> None:
        import undetected_chromedriver as uc  # optional extra: scrape

        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={CORPUS / '.chrome-profile'}")
        self.driver = uc.Chrome(
            options=options, headless=False, version_main=chrome_major()
        )
        self.driver.set_page_load_timeout(60)

    def get(self, url: str) -> bytes:
        self.driver.get(url)
        html = self.driver.page_source
        if self.marker not in html:
            raise BlockedError(self.driver.title or "no post body")
        return html.encode()

    def try_mobile(self, url: str) -> bytes | None:
        """Blogger serves every post at ?m=1 too; the block is per URL."""
        if not self.blogger:
            return None
        try:
            html = self.get(url + "?m=1")
        except Exception:
            return None
        print("  blocked, but the ?m=1 copy loaded", flush=True)
        return html

    def wait_for_human(self, url: str) -> bytes | None:
        """A challenge page (Google's /sorry reCAPTCHA, Cloudflare's "Just a
        moment"): ask the user to solve it in the Chrome window, then poll until
        the page shows up."""
        msg = "Solve the challenge in the Chrome window; fetching resumes by itself"
        print(f"  BLOCKED at {url}\n  {msg}", flush=True)
        notify("garde fetch blocked", msg)
        deadline = time.monotonic() + HUMAN_WAIT
        while time.monotonic() < deadline:
            time.sleep(5)
            try:
                html = self.driver.page_source
            except Exception:
                continue
            if (
                self.marker in html
                and "google.com/sorry" not in self.driver.current_url
            ):
                print("  unblocked, carrying on", flush=True)
                return html.encode()
        print(f"  nobody solved it within {HUMAN_WAIT:.0f}s", flush=True)
        return None

    def restart(self) -> None:
        """Chrome died or the window was closed: open a fresh one."""
        self.close()
        self.start()

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception as e:
            print(f"  (chrome quit: {type(e).__name__})", flush=True)


def load_index() -> dict:
    return json.loads(INDEX.read_text()) if INDEX.exists() else {}


def fetch_browser(urls: list[str], source: str) -> None:
    """Like fetch_all, through Chrome. On a challenge page, waits for the user."""
    PAGES.mkdir(parents=True, exist_ok=True)
    index = load_index()
    todo = [u for u in urls if index.get(u, {}).get("status") != "ok"]
    print(f"{source} (browser): {len(urls)} urls, {len(todo)} to fetch", flush=True)
    browser = Browser(source)
    try:
        for n, url in enumerate(todo, 1):
            if not _fetch_one(browser, url, source, index):
                print(f"  skipping {url} for now", flush=True)
            INDEX.write_text(json.dumps(index, indent=1))
            if n % 10 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
            time.sleep(DELAY)
    finally:
        browser.close()
        INDEX.write_text(json.dumps(index, indent=1))
    ok = sum(1 for u in urls if index.get(u, {}).get("status") == "ok")
    print(f"{source}: {ok}/{len(urls)} cached", flush=True)


def _fetch_one(browser: Browser, url: str, source: str, index: dict) -> bool:
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".html"
    for _attempt in range(3):
        try:
            html = browser.get(url)
        except BlockedError:
            html = browser.try_mobile(url) or browser.wait_for_human(url)
        except Exception as e:  # selenium timeouts and the like
            print(f"  ! {url}: {type(e).__name__}; restarting Chrome", flush=True)
            time.sleep(20)
            browser.restart()
            continue
        if html is None:
            break
        (PAGES / name).write_bytes(html)
        index[url] = {"file": name, "source": source, "status": "ok"}
        return True
    index[url] = {"file": None, "source": source, "status": "error: blocked"}
    return False


def get_patiently(url: str) -> bytes | str:
    """The page, or an error message. Waits out 429s with growing backoff."""
    backoff = 30.0
    while True:
        try:
            return get(url)
        except urllib.error.HTTPError as e:
            if e.code == 429 and backoff <= MAX_BACKOFF:
                wait = float(e.headers.get("Retry-After") or backoff)
                print(f"  429 — waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
                backoff *= 2
                continue
            return f"error: {e}"
        except (urllib.error.URLError, TimeoutError) as e:
            return f"error: {e}"


def fetch_all(urls: list[str], source: str) -> None:
    """Plain urllib. Gives up after MAX_FAILS errors in a row (a block, most
    likely) rather than hammering the site; rerun with --browser."""
    PAGES.mkdir(parents=True, exist_ok=True)
    index = load_index()
    todo = [u for u in urls if index.get(u, {}).get("status") != "ok"]
    print(f"{source}: {len(urls)} urls, {len(todo)} to fetch")
    fails = 0
    for n, url in enumerate(todo, 1):
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".html"
        page = get_patiently(url)
        if isinstance(page, bytes):
            (PAGES / name).write_bytes(page)
            index[url] = {"file": name, "source": source, "status": "ok"}
            fails = 0
        else:
            index[url] = {"file": None, "source": source, "status": page}
            print(f"  ! {url}: {page}", flush=True)
            fails += 1
            if fails >= MAX_FAILS:
                print(f"  {fails} failures in a row; stopping (blocked?)", flush=True)
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


def pinchofnom_urls() -> list[str]:
    """Every recipe in Pinch of Nom's vegan and vegetarian categories. The
    vegetarian one overlaps the vegan one; some entries are meaty recipes with a
    veggie swap, which the import sorts out."""
    if PON_URLS.exists():
        return json.loads(PON_URLS.read_text())
    urls: list[str] = []
    for category in PON_CATEGORIES:
        for page in range(1, 100):
            listing = category + (f"page/{page}/" if page > 1 else "")
            try:
                html = get(listing).decode()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break  # past the last page
                raise
            found = re.findall(r'href="(https://pinchofnom\.com/recipes/[^"]+/)"', html)
            new = [u for u in dict.fromkeys(found) if u not in urls]
            print(f"  {listing}: {len(new)} new", flush=True)
            if not new:
                break
            urls += new
            time.sleep(DELAY)
    CORPUS.mkdir(exist_ok=True)
    PON_URLS.write_text(json.dumps(urls, indent=1))
    return urls


def pinchofnom_picks() -> list[str]:
    """The hand-picked slugs in pinchofnom_picks.txt, checked against the index."""
    index = set(pinchofnom_urls())
    urls = []
    for line in PON_PICKS.read_text().splitlines():
        slug = line.split("#")[0].strip()
        if not slug:
            continue
        url = f"https://pinchofnom.com/recipes/{slug}/"
        if url not in index:
            print(f"  ! {slug} isn't in the vegan/vegetarian index", flush=True)
        urls.append(url)
    return urls


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
    args = sys.argv[1:]
    fetch = fetch_browser if "--browser" in args else fetch_all
    which = [a for a in args if not a.startswith("--")] or ["sandra", "bookmarks"]
    if "pinchofnom" in which:
        fetch(pinchofnom_picks(), "pinchofnom")
    if "bookmarks" in which:
        fetch(bookmark_urls(), "bookmarks")
    if "sandra" in which:
        fetch(sandra_urls(), "vegansandra")
