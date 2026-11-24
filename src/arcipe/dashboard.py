"""
arcipe.dashboard
================
Local web dashboard: edit what's in the fridge, turn the solver's knobs, see the
week, rate what you cooked. Standard library only.

    uv run python -m arcipe.dashboard          # http://127.0.0.1:8765
    uv run python -m arcipe.dashboard --port 9000

Endpoints:
    GET  /            the page
    GET  /api/meta    ingredients, recipes, default weights
    POST /api/plan    {inventory, days, meals, people, max_minutes, weights, effort, seed}
    POST /api/rate    {recipe_id, ingredients, rating, note}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import threading
from dataclasses import asdict
from dataclasses import fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from arcipe.loader import INGREDIENTS
from arcipe.loader import PREPARED_INGREDIENTS
from arcipe.loader import RECIPES
from arcipe.models import MealType
from arcipe.planner import MealPlan
from arcipe.resolver import Resolution
from arcipe.solver import SolverResult
from arcipe.solver import Weights
from arcipe.solver import optimise_week
from arcipe.solver import weights_from
from arcipe.vibes import Feedback


PAGE = Path(__file__).parent / "static" / "dashboard.html"
MEALS: tuple[MealType, ...] = ("breakfast", "lunch", "dinner")
# Search effort slider (1..5) -> (iterations, restarts)
EFFORT = {1: (300, 1), 2: (800, 1), 3: (1500, 2), 4: (3000, 2), 5: (6000, 3)}
_FEEDBACK_LOCK = threading.Lock()


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
            }
            for ing in sorted(INGREDIENTS.values(), key=lambda i: i.name)
        ],
        "recipes": [
            {"id": r.id, "name": r.name, "meal_type": r.meal_type}
            for r in RECIPES.values()
        ],
        "weights": asdict(Weights()),
        "weight_help": {
            "waste": "per 100g that goes off during the week",
            "spill": "per 100g left that goes off just after",
            "unplanned": "per meal with nothing to cook",
            "repeat": "per repeat of a recipe",
            "variety": "per meal, for eating the same kinds of thing",
            "time": "per hour of cooking",
            "liked": "pull towards recipes you've rated well",
        },
        "meals": list(MEALS),
    }


def _display_name(ing_id: str) -> str:
    if ing_id in INGREDIENTS:
        return INGREDIENTS[ing_id].name
    if ing_id in PREPARED_INGREDIENTS:
        return ing_id.replace("_", " ").capitalize()
    return ing_id


def _meal(r: Resolution) -> dict[str, Any]:
    return {
        "recipe_id": r.recipe_id,
        "recipe_name": r.recipe_name,
        "servings": r.servings,
        "minutes": r.timing.total,
        "active_minutes": r.timing.active,
        "make_ahead": [_display_name(i) for i in r.timing.make_ahead],
        "ingredients": sorted(r.used),
        "slots": [
            {
                "label": a.slot_label,
                "items": [
                    {"id": i, "name": _display_name(i), "grams": a.grams.get(i)}
                    for i in a.assigned
                ],
            }
            for a in r.assignments
            if a.assigned
        ],
    }


def _plan(plan: MealPlan) -> dict[str, Any]:
    days = []
    for day in range(1, plan.days + 1):
        meals = []
        for slot in plan.slots:
            if slot.day != day:
                continue
            meals.append(
                {
                    "meal": slot.meal,
                    **(
                        _meal(slot.resolution)
                        if slot.resolution
                        else {"recipe_id": None}
                    ),
                }
            )
        days.append({"day": day, "meals": meals})
    stock = []
    for ing_id in sorted(plan.inventory_start, key=_display_name):
        start = plan.inventory_start[ing_id]
        left = plan.inventory_remaining.get(ing_id, 0.0)
        wasted = plan.wasted.get(ing_id, 0.0)
        stock.append(
            {
                "id": ing_id,
                "name": _display_name(ing_id),
                "start": round(start),
                "used": round(start - left - wasted),
                "left": round(left),
                "wasted": round(wasted),
            }
        )
    return {"days": days, "stock": stock}


def result_json(result: SolverResult) -> dict[str, Any]:
    return {
        "plan": _plan(result.plan),
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


def plan_request(body: dict, feedback: Feedback) -> dict[str, Any]:
    meals = body.get("meals") or ["dinner"]
    if not set(meals) <= set(MEALS):
        raise BadRequestError(f"meals must be from {MEALS}")
    max_minutes = body.get("max_minutes")
    iterations, restarts = EFFORT[_int(body, "effort", 3, 1, 5)]
    known = {f.name for f in fields(Weights)}
    raw_weights = {
        k: float(v) for k, v in (body.get("weights") or {}).items() if k in known
    }
    inventory = {
        k: v for k, v in (body.get("inventory") or {}).items() if v not in ("", None)
    }
    try:
        result = optimise_week(
            inventory=inventory,
            days=_int(body, "days", 7, 1, 14),
            meals_per_day=[m for m in MEALS if m in meals],
            people=_int(body, "people", 2, 1, 12),
            max_minutes=int(max_minutes) if max_minutes else None,
            weights=weights_from(raw_weights),
            iterations=iterations,
            restarts=restarts,
            rng=random.Random(_int(body, "seed", 0, 0, 2**31)),
            feedback=feedback,
        )
    except (KeyError, ValueError) as e:
        raise BadRequestError(str(e).strip("'\"")) from e
    return result_json(result)


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


ROUTES = {"/api/plan": plan_request, "/api/rate": rate_request}


def handle_post(path: str, raw: bytes, feedback: Feedback) -> tuple[int, Any]:
    route = ROUTES.get(path)
    if route is None:
        return HTTPStatus.NOT_FOUND, {"error": "not found"}
    try:
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise BadRequestError("expected a JSON object")
        return HTTPStatus.OK, route(body, feedback)
    except (BadRequestError, json.JSONDecodeError) as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}


def make_handler(feedback: Feedback) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/meta":
                self._json(HTTPStatus.OK, meta())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            status, payload = handle_post(self.path, self.rfile.read(length), feedback)
            self._json(status, payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the terminal quiet

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="arcipe meal planning dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    feedback = Feedback.load(recipes=RECIPES)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(feedback))
    print(f"arcipe dashboard on http://127.0.0.1:{args.port}  (ctrl-c to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
