"""
Fetch recipe pages into a local cache (corpus/pages/), politely.

    uv run python tools/corpus/fetch.py sandra       # every post in vegansandra.com's sitemap
    uv run python tools/corpus/fetch.py bookmarks    # vegan-ish bookmarks from ../recipe-viewer
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
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DELAY = float(os.environ.get("FETCH_DELAY", "4"))
MAX_BACKOFF = 300
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


class Browser:
    """Undetected Chrome, one tab, reused for every page."""

    # Every Blogger post page has this; error and 429 pages don't
    MARKER = "post-body"

    def __init__(self) -> None:
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
        if self.MARKER not in html:
            raise BlockedError(self.driver.title or "no post body")
        return html.encode()

    def try_mobile(self, url: str) -> bytes | None:
        """Blogger serves every post at ?m=1 too; the block is per URL."""
        try:
            html = self.get(url + "?m=1")
        except Exception:
            return None
        print("  blocked, but the ?m=1 copy loaded", flush=True)
        return html

    def wait_for_human(self, url: str) -> bytes | None:
        """A challenge page (Google's /sorry reCAPTCHA): ask the user to solve it in
        the Chrome window, then poll until the post shows up."""
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
                self.MARKER in html
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
    browser = Browser()
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
    args = sys.argv[1:]
    fetch = fetch_browser if "--browser" in args else fetch_all
    which = [a for a in args if not a.startswith("--")] or ["sandra", "bookmarks"]
    if "bookmarks" in which:
        fetch(bookmark_urls(), "bookmarks")
    if "sandra" in which:
        fetch(sandra_urls(), "vegansandra")
