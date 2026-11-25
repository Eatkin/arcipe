"""
garde.pantry
=============
What's in the kitchen, kept between visits: the dashboard's Pantry tab.

Stored as JSON at $GARDE_HOME/pantry.json (default ~/.garde):

    {"items": [{"id": "spinach", "amount": "1", "use_by": "2026-11-27"}, ...],
     "dishes": [{"recipe_id": "chilli", "portions": 2, "cooked": "2026-11-25",
                 "use_by": "2026-11-28", "grams_per_portion": 310}, ...]}

`amount` is what the planner's inventory takes: a count ("3" carrots) or a
measure ("500g"). `use_by` is the last day it's good, or null for "the usual
shelf life from the plan's start" (tins, dried things). Two items with the same
id are two separate lots, say an open block of tofu and a fresh one.

`dishes` are cooked leftovers: portions of a recipe waiting to be eaten. The
planner can serve them as leftovers meals (see to_leftovers).

take() and give() move grams out of and back into the items, for ticking off a
cooked meal (garde.plans).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.models import Ingredient
from garde.models import Recipe
from garde.planner import InventoryEntry
from garde.planner import Leftover
from garde.planner import parse_amount
from garde.resolver import Resolution
from garde.utils import garde_home


# Less than this many grams left of something counts as none
EMPTY_GRAMS = 1.0
# A portion of a dish whose weight we don't know (typed in by hand), so letting
# it go off still counts as waste. A guess
PORTION_GRAMS = 300.0


def default_path() -> Path:
    return garde_home() / "pantry.json"


@dataclass(frozen=True)
class Item:
    id: str
    amount: str
    use_by: str | None = None  # ISO date, last good day

    def use_by_date(self) -> date | None:
        return date.fromisoformat(self.use_by) if self.use_by else None


@dataclass(frozen=True)
class Dish:
    """Cooked portions waiting to be eaten."""

    recipe_id: str
    portions: float
    use_by: str  # ISO date, last good day
    cooked: str | None = None  # ISO date
    grams_per_portion: float = 0.0  # stocked grams per portion, for waste figures


@dataclass
class Pantry:
    items: list[Item] = field(default_factory=list)
    dishes: list[Dish] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "items": [asdict(i) for i in self.items],
            "dishes": [asdict(d) for d in self.dishes],
        }


def _iso(value: Any, what: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what} must be a date") from e


def validate(raw: Any, db: dict[str, Ingredient] | None = None) -> list[Item]:
    """Items from untrusted JSON. Raises ValueError with a readable message."""
    db = db or INGREDIENTS
    if not isinstance(raw, list):
        raise ValueError("items must be a list")
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each item must be an object")
        ing_id = entry.get("id")
        if ing_id not in db:
            raise ValueError(f"unknown ingredient {ing_id!r}")
        amount = str(entry.get("amount") or "").strip()
        if not amount:
            continue  # a row still being filled in
        parse_amount(db[ing_id], _measured(db[ing_id], amount))
        use_by = _iso(entry.get("use_by"), f"{ing_id}: use_by")
        items.append(Item(ing_id, amount, use_by))
    return items


def validate_dishes(raw: Any, recipes: dict[str, Recipe] | None = None) -> list[Dish]:
    """Dishes from untrusted JSON. Raises ValueError with a readable message."""
    recipes = recipes or RECIPES
    if not isinstance(raw, list):
        raise ValueError("dishes must be a list")
    dishes = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each dish must be an object")
        recipe_id = entry.get("recipe_id")
        if recipe_id not in recipes:
            raise ValueError(f"unknown recipe {recipe_id!r}")
        try:
            portions = float(entry.get("portions") or 0)
            per_portion = float(entry.get("grams_per_portion") or 0)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{recipe_id}: portions must be a number") from e
        if not math.isfinite(portions) or portions <= 0:
            continue  # all eaten
        use_by = _iso(entry.get("use_by"), f"{recipe_id}: use_by")
        if use_by is None:
            raise ValueError(f"{recipe_id}: a dish needs a use_by date")
        cooked = _iso(entry.get("cooked"), f"{recipe_id}: cooked")
        dishes.append(Dish(recipe_id, portions, use_by, cooked, per_portion))
    return dishes


def _measured(ing: Ingredient, amount: str) -> str | float:
    """A bare number is a count if the thing has a natural unit, else grams."""
    try:
        number = float(amount)
    except ValueError:
        return amount
    return number if ing.unit_weight else f"{amount}g"


def load(path: Path | None = None) -> Pantry:
    path = path or default_path()
    if not path.exists():
        return Pantry()
    raw = json.loads(path.read_text())
    return Pantry(
        items=validate(raw.get("items", [])),
        dishes=validate_dishes(raw.get("dishes", [])),
    )


def save(stock: Pantry, path: Path | None = None) -> Path:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stock.to_json(), indent=1))
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# To the planner
# ---------------------------------------------------------------------------


def _plan_day(when: date, start: date) -> int:
    """Plan day number of a date: the start is day 1."""
    return (when - start).days + 1


def to_inventory(
    items: list[Item],
    start: date,
    db: dict[str, Ingredient] | None = None,
) -> dict[str, list[InventoryEntry]]:
    """
    The planner's inventory for a plan starting on `start` (day 1). Things past
    their use-by date are left out.
    """
    db = db or INGREDIENTS
    out: dict[str, list[InventoryEntry]] = {}
    for item in items:
        amount = _measured(db[item.id], item.amount)
        use_by = item.use_by_date()
        if use_by is None:
            out.setdefault(item.id, []).append(amount)
            continue
        if use_by < start:
            continue
        expires = _plan_day(use_by, start)
        out.setdefault(item.id, []).append({"amount": amount, "expires": expires})
    return out


def to_leftovers(
    dishes: list[Dish],
    start: date,
    recipes: dict[str, Recipe] | None = None,
) -> list[Leftover]:
    """The planner's leftovers for a plan starting on `start`, past ones left out."""
    recipes = recipes or RECIPES
    out = []
    for dish in dishes:
        use_by = date.fromisoformat(dish.use_by)
        if use_by < start:
            continue
        recipe = recipes[dish.recipe_id]
        cooked = date.fromisoformat(dish.cooked) if dish.cooked else start
        out.append(
            Leftover(
                recipe_id=recipe.id,
                portions=dish.portions,
                grams_per_portion=dish.grams_per_portion or PORTION_GRAMS,
                cooked_day=_plan_day(cooked, start),
                expires_day=_plan_day(use_by, start),
                meal_types=tuple(recipe.meal_type),
                resolution=Resolution(
                    recipe_id=recipe.id,
                    recipe_name=recipe.name,
                    assignments=[],
                    used=set(),
                    unfillable=[],
                    servings=math.ceil(dish.portions),
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Grams in and out
# ---------------------------------------------------------------------------


def grams(item: Item, db: dict[str, Ingredient] | None = None) -> float:
    db = db or INGREDIENTS
    ing = db[item.id]
    return parse_amount(ing, _measured(ing, item.amount))


def with_grams(item: Item, g: float, db: dict[str, Ingredient] | None = None) -> Item:
    """The same item holding `g` grams, still a count if it was one."""
    db = db or INGREDIENTS
    ing = db[item.id]
    if ing.unit_weight and isinstance(_measured(ing, item.amount), float):
        return replace(item, amount=f"{round(g / ing.unit_weight, 2):g}")
    return replace(item, amount=f"{round(g):g}g")


def _soonest_first(item: Item) -> tuple[int, str]:
    return (0, item.use_by) if item.use_by else (1, "")


def take(
    items: list[Item],
    consumed: dict[str, float],
    db: dict[str, Ingredient] | None = None,
) -> tuple[list[Item], list[Item]]:
    """
    Take grams of each ingredient from the items, soonest use-by first. Anything
    not in the pantry (staples, things bought and never entered) is skipped.
    Returns (items left, what was taken: one Item per lot touched, in grams).
    """
    db = db or INGREDIENTS
    want = {k: v for k, v in consumed.items() if v > 0}
    order = sorted(range(len(items)), key=lambda i: _soonest_first(items[i]))
    out: list[Item | None] = list(items)
    taken = []
    for i in order:
        item = items[i]
        need = want.get(item.id, 0.0)
        if need <= 0:
            continue
        have = grams(item, db)
        bite = min(need, have)
        want[item.id] = need - bite
        taken.append(with_grams(item, bite, db))
        left = have - bite
        out[i] = with_grams(item, left, db) if left >= EMPTY_GRAMS else None
    return [i for i in out if i is not None], taken


def give(
    items: list[Item], added: list[Item], db: dict[str, Ingredient] | None = None
) -> list[Item]:
    """Put things back: onto an item with the same id and use-by, or as a new one."""
    db = db or INGREDIENTS
    out = list(items)
    for new in added:
        g = grams(new, db)
        if g < EMPTY_GRAMS:
            continue
        same = next(
            (n for n, i in enumerate(out) if (i.id, i.use_by) == (new.id, new.use_by)),
            None,
        )
        if same is None:
            out.append(with_grams(new, g, db))
        else:
            out[same] = with_grams(out[same], grams(out[same], db) + g, db)
    return out


def eat(
    dishes: list[Dish], recipe_id: str, portions: float
) -> tuple[list[Dish], list[Dish]]:
    """
    Eat portions of a cooked dish, soonest use-by first. Returns (dishes left,
    what was eaten from each, empty if there was none of it).
    """
    out = list(dishes)
    eaten = []
    for i in sorted(range(len(out)), key=lambda i: out[i].use_by):
        dish = out[i]
        if dish.recipe_id != recipe_id or portions <= 1e-9:
            continue
        bite = min(portions, dish.portions)
        portions -= bite
        eaten.append(replace(dish, portions=bite))
        out[i] = replace(dish, portions=dish.portions - bite)
    return [d for d in out if d.portions > 1e-9], eaten


def store(dishes: list[Dish], dish: Dish) -> list[Dish]:
    """Add portions: onto the same dish cooked the same day, or as a new one."""
    for i, d in enumerate(dishes):
        if (d.recipe_id, d.cooked, d.use_by) == (
            dish.recipe_id,
            dish.cooked,
            dish.use_by,
        ):
            return [
                *dishes[:i],
                replace(d, portions=d.portions + dish.portions),
                *dishes[i + 1 :],
            ]
    return [*dishes, dish]
