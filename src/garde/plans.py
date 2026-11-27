"""
garde.plans
============
Saved plans, and ticking off what we cooked: the dashboard's Plans tab.

Each plan is one JSON file at $GARDE_HOME/plans/<id>.json:

    {"id": "20261125-183012-ab12", "name": "Week from Tue 1 Dec",
     "saved_at": "2026-11-25T18:30:12", "start_date": "2026-12-01", "people": 2,
     "settings": {...the request that planned it...},
     "plan": {...dashboard.result_json's plan...},
     "breakdown": {...}}

Every planned meal carries `consumed` (stocked grams of each raw ingredient) and
`made` (grams of made ingredients cooked from their source). Ticking it off as
cooked (cook) changes the pantry:

    a cooked meal    takes its `consumed` grams out of the items, soonest use-by
                     first. A pot of beans cooked a whole batch at a time puts the
                     rest of the pot in as cooked beans. Portions beyond `people`
                     go in as a dish if the recipe keeps.
    a leftovers meal eats `people` portions of that recipe's dish (or, for a
                     mix, `leftover_portions` of each recipe).

What changed is kept on the meal under `cooked`, so untick puts it back.

Shopping: each row of `plan.shopping` can be ticked off as bought (shop), which
puts its packs in the pantry with the usual shelf life from that day, and the
row keeps what went in under `bought`. Things bought that weren't on the list
go in `extras` (add_extra, remove_extra). Unticking takes them back out, as far
as they're still there.

Changing the plan: a meal can be `locked` (kept when re-planning), set by hand
to another dish, or `skipped` (everyone's out). Re-planning (replan_setup, then
the solver, then merge_replan) plans the rest of the week from today's pantry:
meals already ticked off and days gone by stay as they were, locked and skipped
meals are pinned, and the shopping list's unbought rows are replaced by what the
new plan needs (a top-up shop today).

Each meal can also carry the `rating` it got (+1 / -1), so it shows on reload.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from collections.abc import Callable
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from garde import pantry
from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.models import Ingredient
from garde.models import Recipe
from garde.pantry import Dish
from garde.pantry import Item
from garde.pantry import Pantry
from garde.solver import DOUBLE
from garde.solver import LEFTOVERS
from garde.solver import SKIP
from garde.utils import garde_home


PLAN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class PlanError(ValueError):
    """Something wrong with a request about plans; the message is for people."""


def plans_dir() -> Path:
    return garde_home() / "plans"


def _path(plan_id: str, root: Path | None = None) -> Path:
    if not PLAN_ID.match(plan_id):
        raise PlanError("no such plan")
    return (root or plans_dir()) / f"{plan_id}.json"


def _write(record: dict[str, Any], root: Path | None = None) -> None:
    path = _path(record["id"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Saving and listing
# ---------------------------------------------------------------------------


def _check_meal(m: Any, db: dict[str, Ingredient], recipes: dict[str, Recipe]) -> None:
    if not isinstance(m, dict) or not isinstance(m.get("meal"), str):
        raise PlanError("each meal needs a `meal`")
    if m.get("recipe_id") is None:
        return
    if m["recipe_id"] not in recipes:
        raise PlanError(f"unknown recipe {m['recipe_id']!r}")
    for key in ("consumed", "made"):
        grams = m.get(key) or {}
        if not isinstance(grams, dict):
            raise PlanError(f"{key} must be an object")
        for ing_id, g in grams.items():
            if ing_id not in db or not isinstance(g, int | float) or g < 0:
                raise PlanError(f"{key}: bad entry {ing_id!r}")


def _check_day(n: int, raw: Any) -> list[Any]:
    """Day `n` of a plan; returns its meals."""
    if not isinstance(raw, dict):
        raise PlanError("each day must be an object")
    day: dict[str, Any] = raw
    if day.get("day") != n:
        raise PlanError("plan days must be numbered from 1")
    try:
        date.fromisoformat(day.get("date"))  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError) as e:
        raise PlanError("each day needs a date") from e
    meals = day.get("meals")
    if not isinstance(meals, list):
        raise PlanError("each day needs a list of meals")
    return meals


def _check_plan(
    plan: Any, db: dict[str, Ingredient], recipes: dict[str, Recipe]
) -> dict[str, Any]:
    """The plan if it's the shape dashboard.result_json makes, else PlanError."""
    days = plan.get("days") if isinstance(plan, dict) else None
    if not isinstance(days, list) or not days:
        raise PlanError("plan needs some days")
    for n, day in enumerate(days, start=1):
        for m in _check_day(n, day):
            _check_meal(m, db, recipes)
    return plan


def _check_shopping(rows: Any, db: dict[str, Ingredient]) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise PlanError("shopping must be a list")
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in db:
            raise PlanError("shopping: unknown ingredient")
        packs = row.get("packs")
        if not isinstance(packs, int) or packs < 1:
            raise PlanError(f"shopping: {row['id']} needs a number of packs")
    return rows


def _people(settings: dict[str, Any]) -> int:
    try:
        people = int(settings.get("people") or 2)
    except (TypeError, ValueError) as e:
        raise PlanError("people must be a number") from e
    if not 1 <= people <= 12:
        raise PlanError("people must be between 1 and 12")
    return people


def default_name(start: date) -> str:
    return f"Week from {start:%a} {start.day} {start:%b}"


def save(
    result: Any,
    settings: Any = None,
    name: str | None = None,
    now: datetime | None = None,
    root: Path | None = None,
    db: dict[str, Ingredient] | None = None,
    recipes: dict[str, Recipe] | None = None,
) -> dict[str, Any]:
    """Save a dashboard plan (result_json's output). Returns the saved record."""
    if not isinstance(result, dict):
        raise PlanError("result must be an object")
    settings = settings if isinstance(settings, dict) else {}
    db = db or INGREDIENTS
    plan = _check_plan(result.get("plan"), db, recipes or RECIPES)
    plan["shopping"] = _check_shopping(plan.get("shopping"), db)
    for row in plan["shopping"]:
        row.setdefault("bought", None)
    now = (now or datetime.now()).replace(microsecond=0)
    start = date.fromisoformat(plan["days"][0]["date"])
    for day in plan["days"]:
        for m in day["meals"]:
            m.setdefault("cooked", None)
    record = {
        "id": f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        "name": (name or "").strip()[:80] or default_name(start),
        "saved_at": now.isoformat(),
        "start_date": start.isoformat(),
        "people": _people(settings),
        "settings": settings,
        "plan": plan,
        "breakdown": result.get("breakdown") or {},
        "extras": [],
    }
    _write(record, root)
    return record


def write(record: dict[str, Any], root: Path | None = None) -> None:
    _write(record, root)


def rename(record: dict[str, Any], name: Any) -> dict[str, Any]:
    name = str(name or "").strip()[:80]
    if not name:
        raise PlanError("a plan needs a name")
    record["name"] = name
    return record


def load(plan_id: str, root: Path | None = None) -> dict[str, Any]:
    path = _path(plan_id, root)
    if not path.exists():
        raise PlanError("no such plan")
    return json.loads(path.read_text())


def delete(plan_id: str, root: Path | None = None) -> None:
    path = _path(plan_id, root)
    if not path.exists():
        raise PlanError("no such plan")
    path.unlink()


def unticked(record: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Meals on days before `today` that were planned but never ticked off or
    skipped: [{day, date, meal, recipe_name}]. Their food is still counted as
    in the pantry, so they're worth asking about."""
    out = []
    for d in record["plan"]["days"]:
        if date.fromisoformat(d["date"]) >= today:
            break
        for m in d["meals"]:
            if m.get("recipe_id") and not m.get("cooked") and not m.get("skipped"):
                out.append(
                    {
                        "day": d["day"],
                        "date": d["date"],
                        "meal": m["meal"],
                        "recipe_name": m.get("recipe_name"),
                        "leftovers": m.get("leftovers_from") is not None,
                    }
                )
    return out


def summary(record: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    meals = [
        m for d in record["plan"]["days"] for m in d["meals"] if m.get("recipe_id")
    ]
    shopping = record["plan"].get("shopping") or []
    return {
        "id": record["id"],
        "name": record["name"],
        "saved_at": record["saved_at"],
        "start_date": record["start_date"],
        "days": len(record["plan"]["days"]),
        "meals": len(meals),
        "cooked": sum(1 for m in meals if m.get("cooked")),
        "skipped": sum(
            1 for d in record["plan"]["days"] for m in d["meals"] if m.get("skipped")
        ),
        "to_buy": len(shopping),
        "bought": sum(1 for row in shopping if row.get("bought")),
        "end_date": record["plan"]["days"][-1]["date"],
        "unticked": len(unticked(record, today)),
    }


def list_plans(root: Path | None = None) -> list[dict[str, Any]]:
    """Summaries of every saved plan, newest first."""
    folder = root or plans_dir()
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        if PLAN_ID.match(path.stem):
            out.append(summary(json.loads(path.read_text())))
    return out


# ---------------------------------------------------------------------------
# Ticking off
# ---------------------------------------------------------------------------


@dataclass
class Cooked:
    """What ticking a meal off did to the pantry, so it can be undone."""

    on: str  # ISO date it was cooked or eaten
    taken: list[Item] = field(default_factory=list)  # out of the items
    added: list[Item] = field(default_factory=list)  # into the items (a pot of beans)
    dish: Dish | None = None  # spare portions put away
    eaten: list[Dish] = field(default_factory=list)  # leftovers eaten
    instead: dict[str, Any] | None = None  # what was really cooked, if not the plan
    opened: list[dict[str, Any]] = field(default_factory=list)  # tins broken into

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Cooked:
        return cls(
            on=raw["on"],
            taken=[Item(**i) for i in raw.get("taken", [])],
            added=[Item(**i) for i in raw.get("added", [])],
            dish=Dish(**raw["dish"]) if raw.get("dish") else None,
            eaten=[Dish(**d) for d in raw.get("eaten", [])],
            instead=raw.get("instead"),
            opened=raw.get("opened", []),
        )


def find_meal(record: dict[str, Any], day: int, meal: str) -> dict[str, Any]:
    for d in record["plan"]["days"]:
        if d["day"] != day:
            continue
        for m in d["meals"]:
            if m["meal"] == meal:
                return m
    raise PlanError(f"no {meal} on day {day}")


def _plus_days(when: date, days: int | None) -> str | None:
    return None if days is None else (when + timedelta(days=days)).isoformat()


def _batch_rest(
    items: list[Item], made: dict[str, float], when: date, db: dict[str, Ingredient]
) -> tuple[list[Item], list[Item], list[Item]]:
    """
    A pot of beans is cooked a whole batch at a time: the rest of the pot goes in
    as cooked beans (planner.cook_batches does the same in the plan).
    Returns (items, taken for it, added).
    """
    taken: list[Item] = []
    added: list[Item] = []
    for raw, g in made.items():
        made_from = db[raw].made_from
        if made_from is None or made_from.batch is None:
            continue
        batches = math.ceil(g / made_from.batch - 1e-9)
        source = sum(pantry.grams(i, db) for i in items if i.id == made_from.ingredient)
        extra = min(batches * made_from.batch - g, source / made_from.ratio)
        if extra < db[raw].crumb:
            continue
        items, took = pantry.take(
            items, {made_from.ingredient: extra * made_from.ratio}, db
        )
        taken += took
        new = Item(raw, f"{extra:.1f}g", _plus_days(when, made_from.keeps_days))
        items = pantry.give(items, [new], db)
        added.append(new)
    return items, taken, added


def _spare_dish(
    m: dict[str, Any], people: int, when: date, recipes: dict[str, Recipe]
) -> Dish | None:
    """The portions nobody ate tonight, if the dish keeps."""
    recipe = recipes[m["recipe_id"]]
    servings = float(m.get("servings") or 0)
    spare = servings - people
    if spare <= 1e-9 or recipe.keeps_days <= 0:
        return None
    total = sum((m.get("consumed") or {}).values())
    return Dish(
        recipe_id=recipe.id,
        portions=spare,
        use_by=(when + timedelta(days=recipe.keeps_days)).isoformat(),
        cooked=when.isoformat(),
        grams_per_portion=round(total / servings, 1),
    )


def _cook_meal(
    m: dict[str, Any],
    people: int,
    stock: Pantry,
    when: date,
    db: dict[str, Ingredient],
    recipes: dict[str, Recipe],
) -> tuple[Pantry, Cooked]:
    done = Cooked(on=when.isoformat())
    if m.get("leftovers_from") is not None:
        dishes = stock.dishes
        mix = m.get("leftover_portions") or {m["recipe_id"]: people}
        for recipe_id, portions in mix.items():
            dishes, eaten = pantry.eat(dishes, recipe_id, portions)
            done.eaten += eaten
        return Pantry(stock.items, dishes), done
    items, done.taken = pantry.take(stock.items, m.get("consumed") or {}, db)
    items, more, done.added = _batch_rest(items, m.get("made") or {}, when, db)
    done.taken += more
    items, done.opened = pantry.open_items(items, done.taken, when, db)
    dishes = stock.dishes
    done.dish = _spare_dish(m, people, when, recipes)
    if done.dish is not None:
        dishes = pantry.store(dishes, done.dish)
    return Pantry(items, dishes), done


def _remove_items(
    items: list[Item], gone: list[Item], db: dict[str, Ingredient]
) -> list[Item]:
    """Take out exactly these lots (by id and use-by), as far as they're still there."""
    out = list(items)
    for lot in gone:
        g = pantry.grams(lot, db)
        for i, item in enumerate(out):
            if (item.id, item.use_by) != (lot.id, lot.use_by) or g <= 0:
                continue
            have = pantry.grams(item, db)
            bite = min(g, have)
            g -= bite
            out[i] = pantry.with_grams(item, have - bite, db)
    return [i for i in out if pantry.grams(i, db) >= pantry.EMPTY_GRAMS]


def _remove_dish(dishes: list[Dish], gone: Dish) -> list[Dish]:
    """Take these portions back out, matched on recipe and the day it was cooked
    (not use-by: it may have gone in the freezer since)."""
    out = []
    left = gone.portions
    for d in dishes:
        same = (d.recipe_id, d.cooked) == (gone.recipe_id, gone.cooked)
        if same and left > 1e-9:
            bite = min(left, d.portions)
            left -= bite
            d = replace(d, portions=d.portions - bite)
        if d.portions > 1e-9:
            out.append(d)
    return out


def _undo(stock: Pantry, done: Cooked, db: dict[str, Ingredient]) -> Pantry:
    items = pantry.unopen_items(stock.items, done.opened, db)
    items = _remove_items(items, done.added, db)
    items = pantry.give(items, done.taken, db)
    dishes = stock.dishes
    if done.dish is not None:
        dishes = _remove_dish(dishes, done.dish)
    for d in done.eaten:
        dishes = pantry.store(dishes, d)
    return Pantry(items, dishes)


def _grams(raw: Any, what: str, db: dict[str, Ingredient]) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlanError(f"{what} must be an object")
    out = {}
    for ing_id, g in raw.items():
        if ing_id not in db or not isinstance(g, int | float) or not 0 <= g < 1e6:
            raise PlanError(f"{what}: bad entry {ing_id!r}")
        if g > 0:
            out[ing_id] = float(g)
    return out


def check_instead(
    raw: Any, db: dict[str, Ingredient], recipes: dict[str, Recipe]
) -> dict[str, Any]:
    """
    What was really cooked, from untrusted JSON: {consumed: {id: grams}} for
    other amounts, plus recipe_id, made and servings for another dish.
    """
    if not isinstance(raw, dict):
        raise PlanError("instead must be an object")
    out: dict[str, Any] = {"consumed": _grams(raw.get("consumed"), "consumed", db)}
    if raw.get("recipe_id") is not None:
        if raw["recipe_id"] not in recipes:
            raise PlanError(f"unknown recipe {raw['recipe_id']!r}")
        out["recipe_id"] = raw["recipe_id"]
        out["made"] = _grams(raw.get("made"), "made", db)
    if raw.get("servings") is not None:
        servings = raw["servings"]
        if not isinstance(servings, int | float) or not 0 < servings <= 50:
            raise PlanError("servings must be a number of portions")
        out["servings"] = servings
    return out


def cook(
    record: dict[str, Any],
    day: int,
    meal: str,
    stock: Pantry,
    cooked: bool = True,
    when: date | None = None,
    db: dict[str, Ingredient] | None = None,
    recipes: dict[str, Recipe] | None = None,
    instead: Any = None,
) -> tuple[dict[str, Any], Pantry]:
    """
    Tick a meal off as cooked (or eaten, for leftovers), or untick it. Returns the
    updated record and pantry; the caller saves both.

    `instead` is for cooking it differently: other amounts ({consumed}), or
    another dish ({recipe_id, consumed, made, servings}); see check_instead.
    """
    db = db or INGREDIENTS
    recipes = recipes or RECIPES
    m = find_meal(record, day, meal)
    if not m.get("recipe_id") and not m.get("cooked") and not (cooked and instead):
        raise PlanError("nothing was planned for that meal")
    if cooked == bool(m.get("cooked")):
        return record, stock  # already that way
    if cooked:
        actual = m
        if instead is not None:
            instead = check_instead(instead, db, recipes)
            actual = {**m, "leftovers_from": None, **instead}
            if "recipe_id" in instead:
                actual.setdefault("servings", record["people"])
        if not actual.get("recipe_id"):
            raise PlanError("which dish was it?")
        stock, done = _cook_meal(
            actual, int(record["people"]), stock, when or date.today(), db, recipes
        )
        done.instead = instead
        m["cooked"] = asdict(done)
    else:
        stock = _undo(stock, Cooked.from_json(m["cooked"]), db)
        m["cooked"] = None
    return record, stock


def update(
    plan_id: str,
    change: Callable[[dict[str, Any], Pantry], tuple[dict[str, Any], Pantry]],
    root: Path | None = None,
    pantry_path: Path | None = None,
) -> tuple[dict[str, Any], Pantry]:
    """Apply `change` (cook, shop, ...) to the saved plan and pantry; write both."""
    record, stock = change(load(plan_id, root), pantry.load(pantry_path))
    pantry.save(stock, pantry_path)
    _write(record, root)
    return record, stock


def cook_and_save(
    plan_id: str,
    day: int,
    meal: str,
    cooked: bool = True,
    when: date | None = None,
    root: Path | None = None,
    pantry_path: Path | None = None,
    instead: Any = None,
) -> tuple[dict[str, Any], Pantry]:
    """cook() on the saved plan and pantry, writing both back."""
    return update(
        plan_id,
        lambda record, stock: cook(
            record, day, meal, stock, cooked, when, instead=instead
        ),
        root,
        pantry_path,
    )


# ---------------------------------------------------------------------------
# Shopping
# ---------------------------------------------------------------------------


def fresh_use_by(ing: Ingredient, when: date) -> str | None:
    """Last good day of something bought on `when`: its usual shelf life."""
    return _plus_days(
        when, None if ing.shelf_life_days is None else ing.shelf_life_days - 1
    )


def _amount(ing: Ingredient, grams: float) -> str:
    """A count if it comes in whole units, else grams."""
    if ing.unit_weight:
        count = grams / ing.unit_weight
        if abs(count - round(count)) < 1e-6:
            return f"{round(count)}"
    return f"{round(grams)}g"


def _row(record: dict[str, Any], ing_id: str, bought: bool) -> dict[str, Any]:
    """The row to tick (one not yet bought) or untick (one bought). After a
    re-plan the same thing can be on the list twice: bought, and a top-up."""
    rows = [r for r in record["plan"].get("shopping") or [] if r["id"] == ing_id]
    if not rows:
        raise PlanError(f"{ing_id} isn't on the shopping list")
    for row in rows:
        if bool(row.get("bought")) != bought:
            return row
    return rows[0]


def shop(
    record: dict[str, Any],
    ing_id: str,
    stock: Pantry,
    bought: bool = True,
    when: date | None = None,
    db: dict[str, Ingredient] | None = None,
) -> tuple[dict[str, Any], Pantry]:
    """Tick a shopping-list row off as bought (into the pantry), or untick it."""
    db = db or INGREDIENTS
    row = _row(record, ing_id, bought)
    if bought == bool(row.get("bought")):
        return record, stock
    if bought:
        when = when or date.today()
        ing = db[ing_id]
        item = Item(
            ing_id, _amount(ing, row["packs"] * ing.pack_grams), fresh_use_by(ing, when)
        )
        row["bought"] = {**asdict(item), "on": when.isoformat()}
        return record, Pantry(pantry.give(stock.items, [item], db), stock.dishes)
    item = _item(row["bought"])
    row["bought"] = None
    return record, Pantry(_remove_items(stock.items, [item], db), stock.dishes)


def _item(raw: dict[str, Any]) -> Item:
    return Item(raw["id"], raw["amount"], raw.get("use_by"))


def add_extra(
    record: dict[str, Any],
    ing_id: str,
    amount: str,
    stock: Pantry,
    use_by: str | None = None,
    when: date | None = None,
    db: dict[str, Ingredient] | None = None,
) -> tuple[dict[str, Any], Pantry]:
    """Something bought that wasn't on the list: into the pantry and the plan."""
    db = db or INGREDIENTS
    when = when or date.today()
    if ing_id in db and not use_by:
        use_by = fresh_use_by(db[ing_id], when)
    try:
        items = pantry.validate(
            [{"id": ing_id, "amount": amount, "use_by": use_by}], db
        )
    except ValueError as e:
        raise PlanError(str(e)) from e
    if not items:
        raise PlanError("how much did you buy?")
    record.setdefault("extras", []).append({**asdict(items[0]), "on": when.isoformat()})
    return record, Pantry(pantry.give(stock.items, items, db), stock.dishes)


def remove_extra(
    record: dict[str, Any],
    index: int,
    stock: Pantry,
    db: dict[str, Ingredient] | None = None,
) -> tuple[dict[str, Any], Pantry]:
    """Take an extra back out (bought by mistake, or added twice)."""
    extras = record.get("extras") or []
    if not 0 <= index < len(extras):
        raise PlanError("no such extra")
    item = _item(extras.pop(index))
    return record, Pantry(
        _remove_items(stock.items, [item], db or INGREDIENTS), stock.dishes
    )


# ---------------------------------------------------------------------------
# Ratings and locks
# ---------------------------------------------------------------------------


def rate(record: dict[str, Any], day: int, meal: str, rating: int) -> dict[str, Any]:
    """Rate a meal +1 or -1, or 0 to take the rating back."""
    if rating not in (-1, 0, 1):
        raise PlanError("rating must be 1, -1 or 0")
    m = find_meal(record, day, meal)
    if not m.get("recipe_id"):
        raise PlanError("nothing was planned for that meal")
    m["rating"] = rating or None
    return record


def lock(record: dict[str, Any], day: int, meal: str, locked: bool) -> dict[str, Any]:
    """Keep this meal as it is when the rest of the week is re-planned."""
    m = find_meal(record, day, meal)
    if locked and not m.get("recipe_id"):
        raise PlanError("nothing to lock there")
    m["locked"] = bool(locked)
    return record


# ---------------------------------------------------------------------------
# Re-planning the rest of the week
# ---------------------------------------------------------------------------


def pick_of(
    m: dict[str, Any], people: int, recipes: dict[str, Recipe] | None = None
) -> str | None:
    """The solver's pick behind a saved meal (older saves don't record it)."""
    if m.get("skipped"):
        return SKIP
    if not m.get("recipe_id"):
        return None
    if m.get("pick"):
        return m["pick"]
    if m.get("leftovers_from") is not None:
        return LEFTOVERS
    recipe = (recipes or RECIPES)[m["recipe_id"]]
    double = not recipe.batch and float(m.get("servings") or 0) >= 2 * people
    return recipe.id + DOUBLE if double else recipe.id


@dataclass
class Replan:
    """What to hand the solver to re-plan a saved plan from `start` on."""

    from_day: int  # the plan's day number that `start` is
    start: date
    days: int
    meals: list[str]
    pins: dict[tuple[int, str], str | None]  # keyed by the new plan's days
    choice: list[str | None]  # the saved week from `start`, as solver picks

    def new_day(self, day: int) -> int:
        """The re-plan's day number for one of the saved plan's days."""
        return day - self.from_day + 1


def _from_day(record: dict[str, Any], when: date) -> int:
    start = date.fromisoformat(record["start_date"])
    from_day = max(1, (when - start).days + 1)
    if from_day > len(record["plan"]["days"]):
        raise PlanError("this plan is over: plan a new week instead")
    return from_day


def set_meal(
    record: dict[str, Any], day: int, meal: str, pick: str | None
) -> dict[str, Any]:
    """Fix a meal by hand ahead of a re-plan: a dish, leftovers, or SKIP."""
    m = find_meal(record, day, meal)
    if m.get("cooked"):
        raise PlanError("that meal's ticked off already: untick it first")
    if (
        pick not in (SKIP, LEFTOVERS)
        and (pick or "").removesuffix(DOUBLE) not in RECIPES
    ):
        raise PlanError(f"unknown recipe {pick!r}")
    m["skipped"] = pick == SKIP
    m["pick"] = pick
    m["locked"] = pick != SKIP
    return record


def unskip(record: dict[str, Any], day: int, meal: str) -> dict[str, Any]:
    m = find_meal(record, day, meal)
    m["skipped"] = False
    m["pick"] = None
    m["locked"] = False
    return record


def replan_setup(record: dict[str, Any], when: date) -> Replan:
    """
    Re-plan from `when` (or the plan's start, if that's later) to the end:
    ticked-off meals are done (their food is out of the pantry already), so
    they're pinned as SKIP; locked and skipped meals keep their pick.
    """
    from_day = _from_day(record, when)
    days = record["plan"]["days"][from_day - 1 :]
    people = int(record["people"])
    meals = [m["meal"] for m in days[0]["meals"]]
    rp = Replan(
        from_day=from_day,
        start=date.fromisoformat(days[0]["date"]),
        days=len(days),
        meals=meals,
        pins={},
        choice=[],
    )
    for day in days:
        n = rp.new_day(day["day"])
        for m in day["meals"]:
            pick = pick_of(m, people)
            if m.get("cooked"):
                pick = SKIP
                rp.pins[(n, m["meal"])] = SKIP
            elif m.get("skipped") or m.get("locked"):
                rp.pins[(n, m["meal"])] = pick
            rp.choice.append(pick)
    return rp


def _merged_meal(
    old: dict[str, Any], new: dict[str, Any], rp: Replan
) -> dict[str, Any]:
    if old.get("cooked"):
        return old
    if new.get("leftovers_from") is not None:
        new["leftovers_from"] += rp.from_day - 1
    new["cooked"] = None
    new["locked"] = bool(old.get("locked")) and not new.get("skipped")
    if old.get("rating") and old.get("recipe_id") == new.get("recipe_id"):
        new["rating"] = old["rating"]
    return new


def merge_replan(
    record: dict[str, Any], result: dict[str, Any], rp: Replan, when: date
) -> dict[str, Any]:
    """
    Put a re-plan (dashboard.result_json for rp) into the saved plan: its days
    from rp.start on, and its shopping in place of the rows not yet bought.
    """
    new_days = result["plan"]["days"]
    for new_day in new_days:
        day = record["plan"]["days"][rp.from_day + new_day["day"] - 2]
        old = {m["meal"]: m for m in day["meals"]}
        day["meals"] = [
            _merged_meal(old[m["meal"]], m, rp) if m["meal"] in old else m
            for m in new_day["meals"]
        ]
    plan = record["plan"]
    kept = [row for row in plan.get("shopping") or [] if row.get("bought")]
    fresh = [
        {**row, "bought": None, "listed": when.isoformat()}
        for row in result["plan"].get("shopping") or []
    ]
    plan["shopping"] = kept + fresh
    for key in ("stock", "leftovers_wasted", "leftovers_left"):
        plan[key] = result["plan"].get(key, [])
    record["breakdown"] = result.get("breakdown") or {}
    record.setdefault("replans", []).append(
        {"on": when.isoformat(), "from_day": rp.from_day}
    )
    return record
