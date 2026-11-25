"""
Browser smoke test of the dashboard: plan, save, tick a meal and a shopping row,
skip a meal, re-plan. Needs the `ui` extra and a Playwright browser:

    uv run --extra ui playwright install chromium     # once
    uv run --extra dev --extra ui pytest tests/ui

Skipped when Playwright isn't installed.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from garde.dashboard import make_handler
from garde.loader import RECIPES
from garde.vibes import Feedback


sync_api = pytest.importorskip("playwright.sync_api")

PANTRY = {
    "items": [
        {"id": "red_lentils", "amount": "1000g", "use_by": None},
        {"id": "potato", "amount": "10", "use_by": None},
        {"id": "carrot", "amount": "10", "use_by": None},
        {"id": "cannellini_beans", "amount": "4", "use_by": None},
    ],
    "dishes": [],
}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("GARDE_HOME", str(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Feedback(recipes=RECIPES)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    req = urllib.request.Request(
        url + "/api/pantry",
        data=json.dumps(PANTRY).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()
    yield url
    srv.shutdown()


@pytest.fixture
def page(server):
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"no browser: {e}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console", lambda m: errors.append(m.text) if m.type == "error" else None
        )
        page.goto(server)
        page.wait_for_function(
            "document.getElementById('status').textContent !== 'loading…'"
        )
        yield page
        assert not errors, errors
        assert not page.locator("#error").is_visible(), page.locator(
            "#error"
        ).inner_text()
        browser.close()


def test_plan_save_tick_skip_replan(page):
    page.fill("#days", "3")
    page.dispatch_event("#days", "input")
    page.check("#shopping")
    page.click("#plan-btn")
    page.wait_for_selector("#days-grid .meal .name", timeout=60_000)
    assert page.locator("#days-grid .day").count() == 3
    assert page.locator("#metrics .delta").count() >= 4

    page.click("#save-btn")
    page.wait_for_selector("#saved-grid .meal")
    assert page.url.endswith(tuple("0123456789abcdef"))

    # Tick the first dinner off
    first = page.locator("#saved-grid .meal").first
    first.locator(".cook input").check()
    page.wait_for_selector("#saved-grid .meal.done")
    assert page.inner_text("#saved-grid .meal.done .took")  # what came out, if anything

    # Skip the last dinner, then bring the week back up to date
    last = page.locator("#saved-grid .meal").last
    last.locator("button[data-do=skip]").click()
    page.wait_for_selector("#saved-grid .skipped")
    page.click("#replan-btn")
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('re-planned')"
    )
    assert page.locator("#saved-grid .skipped").count() == 1
    assert page.locator("#saved-grid .meal.done").count() == 1

    # Swap options for the middle day
    middle = page.locator("#saved-grid .day").nth(1)
    middle.locator("button[data-do=swap]").click()
    page.wait_for_selector("#saved-grid .options .hint")

    # Rename
    page.click("#saved-rename")
    page.fill("#saved-name input", "Advent week")
    page.press("#saved-name input", "Enter")
    page.wait_for_function(
        "document.querySelector('#plans-list').textContent.includes('Advent week')"
    )


def test_bin_what_went_off(server, page):
    stale = {"id": "spinach", "amount": "1", "use_by": "2020-01-01"}
    req = urllib.request.Request(
        server + "/api/pantry",
        data=json.dumps({"items": [*PANTRY["items"], stale]}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()
    page.goto(server + "/#pantry")
    page.reload()  # only the hash changed, so load the new pantry
    page.wait_for_selector("#gone-off:not([style*='none'])")
    assert "Spinach" in page.inner_text("#gone-off-text")
    page.click("#gone-off-bin")
    page.wait_for_function(
        "document.getElementById('waste-month').textContent.includes('g.')"
    )
    assert page.locator("#gone-off").is_hidden()
    # A row's own bin button
    page.locator("#areas .prow:not(.head) button.bin").first.click()
    page.wait_for_function(
        "document.getElementById('status').textContent.startsWith('binned')"
    )


def test_shopping_list_by_aisle(server, page):
    req = urllib.request.Request(
        server + "/api/pantry",
        data=json.dumps({"items": [], "dishes": []}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()
    page.reload()
    page.fill("#days", "2")
    page.dispatch_event("#days", "input")
    page.check("#shopping")
    page.click("#plan-btn")
    page.wait_for_selector("#shop .aisle", timeout=60_000)
    page.click("#save-btn")
    page.wait_for_selector("#saved-shop .aisle")
    page.click("#saved-shop button.copy")
    page.wait_for_function(
        "document.querySelector('#saved-shop button.copy').textContent.includes('Copied')"
        " || document.querySelector('.copy-text')"
    )
    page.locator("#saved-shop input[type=checkbox]").first.check()
    page.wait_for_selector("#saved-shop li.got")


def test_staples_toggle(server, page):
    page.goto(server + "/#pantry")
    page.click("#staples-box summary")
    names = page.locator("#staples button").all_text_contents()
    assert names == sorted(names, key=str.lower)
    page.click("#staples button[data-id=cashews]")
    page.wait_for_selector("#staples button[data-id=cashews].off")
    got = json.loads(urllib.request.urlopen(server + "/api/household").read())
    assert got == {"not_kept": ["cashews"]}


def test_bulk_add(server, page):
    page.goto(server + "/#pantry")
    page.click("#bulk summary")
    page.fill("#bulk-list input[data-id=udon]", "300g")
    page.fill("#bulk-list input[data-id=mushy_peas]", "2")
    page.click("#bulk-add")
    page.wait_for_function(
        "document.getElementById('status').textContent === 'pantry saved'"
    )
    got = json.loads(urllib.request.urlopen(server + "/api/pantry").read())
    amounts = {i["id"]: i["amount"] for i in got["items"]}
    assert amounts["udon"] == "300g" and amounts["mushy_peas"] == "2"
    assert page.locator("#bulk-list input[data-id=udon]").input_value() == ""


def test_freeze_a_dish_and_an_item(server, page):
    body = {
        "items": [{"id": "spinach", "amount": "1", "use_by": "2099-01-01"}],
        "dishes": [{"recipe_id": "bean_chilli", "portions": 2, "use_by": "2099-01-01"}],
    }
    req = urllib.request.Request(
        server + "/api/pantry",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()
    page.goto(server + "/#pantry")
    page.reload()
    page.locator("#dishes .drow:not(.head) button.freeze").click()
    page.locator("#areas .prow:not(.head) button.freeze").first.click()
    page.wait_for_selector("#areas section:nth-child(2) .badge.frozen")  # the freezer
    page.wait_for_function(
        "document.getElementById('status').textContent === 'pantry saved'"
    )
    got = json.loads(urllib.request.urlopen(server + "/api/pantry").read())
    assert got["dishes"][0]["frozen"] and got["items"][0]["frozen"]
