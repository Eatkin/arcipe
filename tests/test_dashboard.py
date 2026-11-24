import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from arcipe.dashboard import BadRequestError
from arcipe.dashboard import make_handler
from arcipe.dashboard import meta
from arcipe.dashboard import plan_request
from arcipe.dashboard import rate_request
from arcipe.loader import RECIPES
from arcipe.vibes import Feedback


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCIPE_HOME", str(tmp_path))


def test_meta_lists_everything():
    m = meta()
    assert {r["id"] for r in m["recipes"]} == set(RECIPES)
    assert set(m["weights"]) == set(m["weight_help"])


def test_plan_request_round_trip():
    out = plan_request(
        {
            "inventory": {"spinach": {"amount": 1, "expires": 2}, "potato": 3},
            "days": 2,
            "effort": 1,
            "seed": 1,
        },
        Feedback(recipes=RECIPES),
    )
    assert len(out["plan"]["days"]) == 2
    assert out["breakdown"]["objective"] <= out["greedy"]["objective"] + 1e-9
    json.dumps(out)  # serialisable


@pytest.mark.parametrize(
    "body, message",
    [
        ({"meals": ["brunch"]}, "meals"),
        ({"days": 99}, "days"),
        ({"inventory": {"dragonfruit": 1}}, "dragonfruit"),
        ({"inventory": {"red_lentils": 2}}, "unit_weight"),
        ({"weights": {}, "people": "lots"}, "people"),
    ],
)
def test_plan_request_rejects_bad_input(body, message):
    with pytest.raises(BadRequestError, match=message):
        plan_request({"effort": 1, **body}, Feedback(recipes=RECIPES))


def test_rating_is_saved(tmp_path):
    f = Feedback(recipes=RECIPES)
    out = rate_request(
        {"recipe_id": "rajma", "ingredients": ["tomato"], "rating": 1}, f
    )
    assert out["recipe_score"] > 0
    assert Feedback.load(recipes=RECIPES).recipe_score("rajma") > 0
    with pytest.raises(BadRequestError):
        rate_request({"recipe_id": "rajma", "rating": 3}, f)
    with pytest.raises(BadRequestError):
        rate_request({"recipe_id": "nope", "rating": 1}, f)


def test_server_serves_page_and_api():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(Feedback(recipes=RECIPES))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>arcipe" in page
        req = urllib.request.Request(
            base + "/api/plan",
            data=json.dumps({"days": 1, "effort": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert "plan" in json.loads(urllib.request.urlopen(req).read())
        bad = urllib.request.Request(base + "/api/plan", data=b"[1]")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        server.shutdown()


def test_plan_request_with_shopping_lists_things_to_buy():
    out = plan_request(
        {
            "inventory": {"carrot": 2},
            "days": 3,
            "effort": 1,
            "seed": 0,
            "shopping": True,
        },
        Feedback(recipes=RECIPES),
    )
    shop = out["plan"]["shopping"]
    for row in shop:
        assert {"name", "packs", "grams", "price_word"} <= set(row)
    bought = {row["id"] for row in out["plan"]["stock"] if row["bought"]}
    assert bought == {row["id"] for row in shop}
    assert out["breakdown"]["price_points"] == sum(
        r["price"] * r["packs"] for r in shop
    )
