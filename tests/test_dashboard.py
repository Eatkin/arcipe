import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from garde.dashboard import BadRequestError
from garde.dashboard import make_handler
from garde.dashboard import meta
from garde.dashboard import plan_request
from garde.dashboard import rate_request
from garde.dashboard import solve_args
from garde.dashboard import static_file
from garde.loader import RECIPES
from garde.vibes import Feedback


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GARDE_HOME", str(tmp_path))


def test_meta_lists_everything():
    m = meta()
    assert {r["id"] for r in m["recipes"]} == set(RECIPES)
    assert set(m["weights"]) == set(m["weight_scale"])
    for k, scale in m["weight_scale"].items():
        assert scale["low"] and scale["high"] and scale["label"]
        assert 0 <= m["weights"][k] <= scale["max"]
    assert all(i["storage"] for i in m["ingredients"])


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
        assert "<title>Garde Manger" in page
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


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Feedback(recipes=RECIPES)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _req(url, body=None, method="POST"):
    return urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_sends_progress_then_the_plan(server):
    body = {"days": 2, "effort": 1, "inventory": {"potato": 3}}
    raw = urllib.request.urlopen(_req(server + "/api/plan/stream", body)).read()
    events = _events(raw.decode())
    kinds = [k for k, _ in events]
    assert kinds[-1] == "result"
    assert "progress" in kinds
    last_progress = [d for k, d in events if k == "progress"][-1]
    assert last_progress["done"] == last_progress["total"]
    assert {"wasted_g", "unplanned", "hours"} <= set(last_progress["best"])
    assert len(events[-1][1]["plan"]["days"]) == 2


def test_stream_rejects_bad_input_before_streaming(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(_req(server + "/api/plan/stream", {"days": 99}))
    assert e.value.code == 400


def test_pantry_round_trip_and_plans_from_it(server):
    items = [{"id": "spinach", "amount": "1", "use_by": "2026-12-02"}]
    put = urllib.request.urlopen(_req(server + "/api/pantry", {"items": items}, "PUT"))
    assert json.loads(put.read())["items"] == items
    got = json.loads(urllib.request.urlopen(server + "/api/pantry").read())
    assert got["items"] == items
    args, _ = solve_args({"start_date": "2026-12-01"}, Feedback(recipes=RECIPES))
    assert args["inventory"] == {"spinach": [{"amount": 1.0, "expires": 2}]}


def test_pantry_rejects_unknown_ingredients(server):
    bad = {"items": [{"id": "unicorn", "amount": "1"}]}
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(_req(server + "/api/pantry", bad, "PUT"))
    assert e.value.code == 400


def test_weeknight_and_weekend_limits():
    # 2026-12-04 is a Friday
    args, _ = solve_args(
        {
            "start_date": "2026-12-04",
            "days": 4,
            "weeknight_minutes": 30,
            "weekend_minutes": 0,
            "inventory": {},
        },
        Feedback(recipes=RECIPES),
    )
    assert args["max_minutes"] == [30, None, None, 30]


def test_plan_days_have_dates_and_meals_have_methods():
    out = plan_request(
        {
            "inventory": {"potato": 4, "carrot": 4},
            "days": 2,
            "effort": 1,
            "start_date": "2026-12-01",
        },
        Feedback(recipes=RECIPES),
    )
    assert [d["date"] for d in out["plan"]["days"]] == ["2026-12-01", "2026-12-02"]
    cooked = [m for d in out["plan"]["days"] for m in d["meals"] if m["recipe_id"]]
    assert cooked
    for m in cooked:
        if m["leftovers_from"] is None:
            assert m["steps"][-1] == "Serve."
            assert "description" in m
            for src in m["sources"]:
                assert src["url"].startswith("http") and src["title"]


def test_static_files_stay_in_static():
    assert static_file("/").name == "dashboard.html"
    assert static_file("/static/dashboard.js").name == "dashboard.js"
    assert static_file("/static/dashboard.css").name == "dashboard.css"
    assert static_file("/static/../dashboard.py") is None
    assert static_file("/static/..%2Fdashboard.py") is None
    assert static_file("/static/nope.js") is None
