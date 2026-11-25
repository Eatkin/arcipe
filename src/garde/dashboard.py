"""
garde.dashboard
================
Local web dashboard: keep the pantry, turn the solver's knobs, watch it search,
see the week and how to cook it, rate what you cooked. Standard library only.

    uv run python -m garde.dashboard          # http://127.0.0.1:8765
    uv run python -m garde.dashboard --port 9000

Endpoints:
    GET  /                 the page (static/dashboard.html, .css, .js)
    GET  /api/meta         ingredients, recipes, weights and what they mean
    GET  /api/pantry       {items: [{id, amount, use_by}],
                            dishes: [{recipe_id, portions, use_by, cooked, ...}]}
    PUT  /api/pantry       same shape; saved to $GARDE_HOME/pantry.json. Without
                           `dishes` the saved dishes are kept.
    POST /api/plan         {days, meals, people, start_date, weeknight_minutes,
                            weekend_minutes, max_minutes, weights, effort, seed,
                            shopping, inventory}  -> the plan
                           Without `inventory` it plans from the saved pantry.
    POST /api/plan/stream  same body; Server-Sent Events: `progress` events while
                           it searches, then one `result` (or `error`). Closing
                           the connection stops the search.
    POST /api/rate         {recipe_id, ingredients, rating, note}
    GET  /api/plans        saved plans, newest first: [{id, name, saved_at, ...}]
    POST /api/plans        {result, settings, name} -> the saved plan
                           (result is what /api/plan returned)
    GET  /api/plans/<id>   one saved plan
    DELETE /api/plans/<id>
    POST /api/plans/<id>/cook  {day, meal, cooked: true|false, date}
                           tick a meal off (or back on): takes its ingredients
                           out of the pantry -> {plan, pantry}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from dataclasses import fields
from datetime import date
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from garde import pantry
from garde import plans
from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.method import display_name
from garde.method import steps
from garde.models import MealType
from garde.planner import MealPlan
from garde.resolver import Resolution
from garde.solver import Breakdown
from garde.solver import SolverResult
from garde.solver import Weights
from garde.solver import optimise_week
from garde.solver import weights_from
from garde.vibes import Feedback


STATIC = Path(__file__).parent / "static"
PAGE = STATIC / "dashboard.html"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
MEALS: tuple[MealType, ...] = ("breakfast", "lunch", "dinner")
# Search effort slider (1..5) -> (iterations, restarts)
EFFORT = {1: (300, 1), 2: (800, 1), 3: (1500, 2), 4: (3000, 2), 5: (6000, 3)}
# Seconds between progress events on the stream
PROGRESS_INTERVAL = 0.2

# Each solver weight as a slider with words at both ends. Low = the weight is 0,
# high = `max`. Wording is a first draft
WEIGHT_SCALE: dict[str, dict[str, Any]] = {
    "waste": {
        "label": "Use up food",
        "low": "don't mind binning a bit",
        "high": "use every last scrap",
        "max": 10,
    },
    "spill": {
        "label": "Leave the fridge tidy",
        "low": "fine to carry things over",
        "high": "nothing about to turn at the end",
        "max": 10,
    },
    "unplanned": {
        "label": "Fill every meal",
        "low": "skip a meal if nothing fits",
        "high": "always find something",
        "max": 30,
    },
    "repeat": {
        "label": "Same dish twice",
        "low": "happy to repeat",
        "high": "never the same dish",
        "max": 10,
    },
    "variety": {
        "label": "Mix it up",
        "low": "similar flavours all week is fine",
        "high": "something different each time",
        "max": 10,
    },
    "time": {
        "label": "Time in the kitchen",
        "low": "happy to cook for hours",
        "high": "keep it quick",
        "max": 10,
    },
    "liked": {
        "label": "Stick to favourites",
        "low": "try new things",
        "high": "mostly what we've liked",
        "max": 10,
    },
    "price": {
        "label": "Shopping bill",
        "low": "buy what's needed",
        "high": "keep it cheap",
        "max": 10,
    },
    "leftovers": {
        "label": "Leftovers",
        "low": "love leftovers",
        "high": "cook fresh every time",
        "max": 10,
    },
}
_FEEDBACK_LOCK = threading.Lock()
# Held while the pantry or a saved plan is read, changed and written back
_KITCHEN_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# JSON shapes
# ---------------------------------------------------------------------------


def meta() -> dict[str, Any]:
    return {
        "ingredients": [
            {
                "id": ing.id,
                "name": ing.name,
                "unit_weight": ing.unit_weight,
                "shelf_life_days": ing.shelf_life_days,
                "unlimited": ing.unlimited,
                "price": int(ing.price),
                "price_word": ing.price.word,
                "storage": ing.storage,
            }
            for ing in sorted(INGREDIENTS.values(), key=lambda i: i.name)
        ],
        "recipes": [
            {"id": r.id, "name": r.name, "meal_type": r.meal_type}
            for r in RECIPES.values()
        ],
        "weights": asdict(Weights()),
        "weight_scale": WEIGHT_SCALE,
        "meals": list(MEALS),
        "today": date.today().isoformat(),
    }


def _source(url: str) -> dict[str, str]:
    """A source link with a readable title from its slug."""
    parsed = urlparse(url)
    slug = Path(parsed.path).stem or parsed.netloc
    title = slug.replace("-", " ").replace("_", " ").strip().capitalize()
    return {"url": url, "title": title, "site": parsed.netloc.removeprefix("www.")}


def _meal(r: Resolution) -> dict[str, Any]:
    recipe = RECIPES[r.recipe_id]
    return {
        "recipe_id": r.recipe_id,
        "recipe_name": r.recipe_name,
        "servings": r.servings,
        "minutes": r.timing.total,
        "active_minutes": r.timing.active,
        "make_ahead": [display_name(i) for i in r.timing.make_ahead],
        "ingredients": sorted(r.used),
        "consumed": {i: round(g, 1) for i, g in sorted(r.consumed.items())},
        "made": {i: round(g, 1) for i, g in sorted(r.made.items())},
        "slots": [
            {
                "label": a.slot_label,
                "items": [
                    {"id": i, "name": display_name(i), "grams": a.grams.get(i)}
                    for i in a.assigned
                ],
            }
            for a in r.assignments
            if a.assigned
        ],
        "description": " ".join(recipe.description.split()),
        "notes": recipe.notes,
        "steps": steps(recipe, r),
        "sources": [_source(u) for u in recipe.sources],
    }


def _plan(plan: MealPlan, first_day: date) -> dict[str, Any]:
    days = []
    for day in range(1, plan.days + 1):
        meals = []
        for slot in plan.slots:
            if slot.day != day:
                continue
            meals.append(
                {
                    "meal": slot.meal,
                    "leftovers_from": slot.leftovers_from,
                    "leftovers_from_date": None
                    if slot.leftovers_from is None
                    else (
                        first_day + timedelta(days=slot.leftovers_from - 1)
                    ).isoformat(),
                    **(
                        _meal(slot.resolution)
                        if slot.resolution
                        else {"recipe_id": None}
                    ),
                }
            )
        when = first_day + timedelta(days=day - 1)
        days.append({"day": day, "date": when.isoformat(), "meals": meals})
    stock = []
    bought = {i: n * INGREDIENTS[i].pack_grams for i, n in plan.bought.items()}
    keys = set(plan.inventory_start) | set(bought) | set(plan.made)
    for ing_id in sorted(keys, key=display_name):
        start = plan.inventory_start.get(ing_id, 0.0)
        extra = bought.get(ing_id, 0.0) + plan.made.get(ing_id, 0.0)
        left = plan.inventory_remaining.get(ing_id, 0.0)
        wasted = plan.wasted.get(ing_id, 0.0)
        stock.append(
            {
                "id": ing_id,
                "name": display_name(ing_id),
                "start": round(start),
                "bought": round(bought.get(ing_id, 0.0)),
                "made": round(plan.made.get(ing_id, 0.0)),
                "used": round(start + extra - left - wasted),
                "left": round(left),
                "wasted": round(wasted),
            }
        )
    dishes = [
        {
            "name": RECIPES[key.split(":", 1)[1]].name,
            "wasted": round(grams),
        }
        for key, grams in sorted(plan.wasted.items())
        if key.startswith("leftovers:")
    ]
    left = [
        {"name": RECIPES[d.recipe_id].name, "portions": round(d.portions, 1)}
        for d in plan.leftovers
    ]
    return {
        "days": days,
        "stock": stock,
        "shopping": plan.shopping_items(),
        "leftovers_wasted": dishes,
        "leftovers_left": left,
    }


def result_json(result: SolverResult, start: date | None = None) -> dict[str, Any]:
    return {
        "plan": _plan(result.plan, start or date.today()),
        "breakdown": asdict(result.breakdown),
        "greedy": asdict(result.greedy),
        "evaluations": result.evaluations,
    }


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------


class BadRequestError(ValueError):
    pass


def _int(body: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = body.get(key, default)
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError) as e:
        raise BadRequestError(f"{key} must be a number") from e
    if not lo <= value <= hi:
        raise BadRequestError(f"{key} must be between {lo} and {hi}")
    return value


def _date(body: dict, key: str) -> date:
    value = body.get(key)
    if not value:
        return date.today()
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise BadRequestError(f"{key} must be a date like 2026-12-01") from e


def _minutes(body: dict, key: str) -> int | None:
    return _int(body, key, 0, 0, 24 * 60) or None


def _time_limits(body: dict, start: date, days: int) -> int | list[int | None] | None:
    """One limit for every day, or weeknight / weekend limits (Sat and Sun)."""
    if "weeknight_minutes" not in body and "weekend_minutes" not in body:
        return _minutes(body, "max_minutes")
    weeknight = _minutes(body, "weeknight_minutes")
    weekend = _minutes(body, "weekend_minutes")
    return [
        weekend if (start + timedelta(days=d)).weekday() >= 5 else weeknight
        for d in range(days)
    ]


def _stock(body: dict, start: date) -> tuple[dict, list]:
    """(inventory, leftovers): from the body, or from the saved pantry without one."""
    if "inventory" not in body:
        saved = pantry.load()
        return (
            pantry.to_inventory(saved.items, start),
            pantry.to_leftovers(saved.dishes, start),
        )
    inventory = {
        k: v for k, v in (body.get("inventory") or {}).items() if v not in ("", None)
    }
    return inventory, []


def solve_args(body: dict, feedback: Feedback) -> tuple[dict[str, Any], date]:
    """optimise_week's arguments from a request body, and the plan's start date."""
    meals = body.get("meals") or ["dinner"]
    if not set(meals) <= set(MEALS):
        raise BadRequestError(f"meals must be from {MEALS}")
    iterations, restarts = EFFORT[_int(body, "effort", 3, 1, 5)]
    known = {f.name for f in fields(Weights)}
    raw_weights = {
        k: float(v) for k, v in (body.get("weights") or {}).items() if k in known
    }
    start = _date(body, "start_date")
    days = _int(body, "days", 7, 1, 14)
    inventory, leftovers = _stock(body, start)
    args = {
        "inventory": inventory,
        "leftovers": leftovers,
        "days": days,
        "meals_per_day": [m for m in MEALS if m in meals],
        "people": _int(body, "people", 2, 1, 12),
        "max_minutes": _time_limits(body, start, days),
        "weights": weights_from(raw_weights),
        "iterations": iterations,
        "restarts": restarts,
        "rng": random.Random(_int(body, "seed", 0, 0, 2**31)),
        "feedback": feedback,
        "shopping": bool(body.get("shopping")),
    }
    return args, start


def run_plan(
    body: dict, feedback: Feedback, progress: Callable[..., None] | None = None
) -> dict[str, Any]:
    args, start = solve_args(body, feedback)
    try:
        result = optimise_week(**args, progress=progress)
    except (KeyError, ValueError) as e:
        raise BadRequestError(str(e).strip("'\"")) from e
    return result_json(result, start)


def plan_request(body: dict, feedback: Feedback) -> dict[str, Any]:
    return run_plan(body, feedback)


def progress_json(done: int, total: int, weeks_tried: int, best: Breakdown) -> dict:
    return {
        "done": done,
        "total": total,
        "weeks_tried": weeks_tried,
        "best": {
            "wasted_g": round(best.waste_g + best.spill_g),
            "unplanned": best.unplanned,
            "hours": round(best.minutes / 60, 1),
        },
    }


def rate_request(body: dict, feedback: Feedback) -> dict[str, Any]:
    recipe_id = body.get("recipe_id")
    if recipe_id not in RECIPES:
        raise BadRequestError("unknown recipe")
    with _FEEDBACK_LOCK:
        try:
            feedback.add(
                recipe_id,
                list(body.get("ingredients") or []),
                int(body.get("rating", 0)),
                note=str(body.get("note") or ""),
            )
        except ValueError as e:
            raise BadRequestError(str(e)) from e
        path = feedback.save()
        score = feedback.recipe_score(recipe_id)
    return {"saved": str(path), "recipe_score": score}


def pantry_get(feedback: Feedback) -> dict[str, Any]:
    return pantry.load().to_json()


def pantry_put(body: dict, feedback: Feedback) -> dict[str, Any]:
    with _KITCHEN_LOCK:
        try:
            items = pantry.validate(body.get("items"))
            dishes = (
                pantry.validate_dishes(body["dishes"])
                if "dishes" in body
                else pantry.load().dishes
            )
        except ValueError as e:
            raise BadRequestError(str(e)) from e
        stock = pantry.Pantry(items, dishes)
        pantry.save(stock)
    return stock.to_json()


def plans_list(body: dict, feedback: Feedback) -> dict[str, Any]:
    return {"plans": plans.list_plans()}


def plan_save(body: dict, feedback: Feedback) -> dict[str, Any]:
    return plans.save(body.get("result"), body.get("settings"), body.get("name"))


def plan_get(plan_id: str, body: dict) -> dict[str, Any]:
    return plans.load(plan_id)


def plan_delete(plan_id: str, body: dict) -> dict[str, Any]:
    plans.delete(plan_id)
    return {"deleted": plan_id}


def plan_cook(plan_id: str, body: dict) -> dict[str, Any]:
    day = _int(body, "day", 0, 1, 14)
    meal = body.get("meal")
    if meal not in MEALS:
        raise BadRequestError(f"meal must be one of {MEALS}")
    when = _date(body, "date")
    with _KITCHEN_LOCK:
        record, stock = plans.cook_and_save(
            plan_id, day, meal, cooked=bool(body.get("cooked", True)), when=when
        )
    return {"plan": record, "pantry": stock.to_json()}


ROUTES = {
    "POST /api/plan": plan_request,
    "POST /api/rate": rate_request,
    "PUT /api/pantry": pantry_put,
    "POST /api/plans": plan_save,
    "GET /api/plans": plans_list,
}
# /api/plans/<id>[/cook], by method
PLAN_ROUTES = {
    ("GET", ""): plan_get,
    ("DELETE", ""): plan_delete,
    ("POST", "/cook"): plan_cook,
}
_PLAN_PATH = re.compile(r"^/api/plans/([^/]+)(/cook)?$")


def _body(raw: bytes) -> dict:
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise BadRequestError("expected a JSON object")
    return body


def handle(method: str, url: str, raw: bytes, feedback: Feedback) -> tuple[int, Any]:
    """An API request -> (status, JSON payload)."""
    try:
        route = ROUTES.get(f"{method} {url}")
        if route is not None:
            return HTTPStatus.OK, route(_body(raw), feedback)
        found = _PLAN_PATH.match(url)
        plan_route = found and PLAN_ROUTES.get((method, found.group(2) or ""))
        if not found or not plan_route:
            return HTTPStatus.NOT_FOUND, {"error": "not found"}
        return HTTPStatus.OK, plan_route(found.group(1), _body(raw))
    except plans.PlanError as e:
        status = (
            HTTPStatus.NOT_FOUND if str(e) == "no such plan" else HTTPStatus.BAD_REQUEST
        )
        return status, {"error": str(e)}
    except (BadRequestError, json.JSONDecodeError) as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}


def static_file(path: str) -> Path | None:
    """A file in static/, or None. Nothing outside it, and only page assets."""
    path = urlparse(path).path
    name = "dashboard.html" if path in ("/", "/index.html") else None
    if path.startswith("/static/"):
        name = path.removeprefix("/static/")
    if not name or "/" in name or "\\" in name:
        return None
    file = STATIC / name
    if file.suffix not in CONTENT_TYPES or not file.is_file():
        return None
    return file


class _Stream:
    """Server-Sent Events over a handler's open connection."""

    def __init__(self, wfile: Any) -> None:
        self.wfile = wfile
        self.last = 0.0

    def send(self, event: str, payload: Any) -> None:
        data = json.dumps(payload)
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def progress(
        self, done: int, total: int, weeks_tried: int, best: Breakdown
    ) -> None:
        now = time.monotonic()
        if done < total and now - self.last < PROGRESS_INTERVAL:
            return
        self.last = now
        self.send("progress", progress_json(done, total, weeks_tried, best))


class Handler(BaseHTTPRequestHandler):
    feedback: Feedback

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _raw(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self) -> None:
        file = static_file(self.path)
        if file is not None:
            self._send(HTTPStatus.OK, file.read_bytes(), CONTENT_TYPES[file.suffix])
        elif self.path == "/api/meta":
            self._json(HTTPStatus.OK, meta())
        elif self.path == "/api/pantry":
            self._json(HTTPStatus.OK, pantry_get(self.feedback))
        else:
            self._json(*handle("GET", self.path, b"", self.feedback))

    def do_PUT(self) -> None:
        self._json(*handle("PUT", self.path, self._raw(), self.feedback))

    def do_DELETE(self) -> None:
        self._json(*handle("DELETE", self.path, self._raw(), self.feedback))

    def do_POST(self) -> None:
        if self.path == "/api/plan/stream":
            self._stream()
            return
        self._json(*handle("POST", self.path, self._raw(), self.feedback))

    def _stream(self) -> None:
        try:
            body = _body(self._raw())
            solve_args(body, self.feedback)  # bad input is a plain 400
        except (BadRequestError, json.JSONDecodeError) as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        stream = _Stream(self.wfile)
        try:
            result = run_plan(body, self.feedback, stream.progress)
            stream.send("result", result)
        except BrokenPipeError, ConnectionResetError:
            pass  # they pressed Stop or closed the tab
        except BadRequestError as e:
            with contextlib.suppress(OSError):
                stream.send("error", {"error": str(e)})

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep the terminal quiet


def make_handler(feedback: Feedback) -> type[BaseHTTPRequestHandler]:
    return type("BoundHandler", (Handler,), {"feedback": feedback})


def main() -> None:
    parser = argparse.ArgumentParser(description="garde meal planning dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    feedback = Feedback.load(recipes=RECIPES)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(feedback))
    print(f"garde dashboard on http://127.0.0.1:{args.port}  (ctrl-c to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
