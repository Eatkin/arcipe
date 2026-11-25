"""
garde.planner
==============
Week-level meal planner. Resolves recipes across multiple days, drawing down a
shared stock of ingredient lots and enforcing variety constraints.

Inventory input (see parse_inventory):
    {"carrot": 6}                              — natural units (Ingredient.unit_weight)
    {"red_lentils": "500g"}                    — grams / kg / ml / l
    {"spinach": {"amount": 1, "expires": 2}}   — explicit days until it goes off
    {"tofu": [1, {"amount": 1, "expires": 3}]} — several lots of one thing
Fresh things default to their Ingredient.shelf_life_days. Unlimited staples are
left out of the inventory entirely; listing one makes it tracked.

Stock model:
    A Lot is some grams of one ingredient with the last day it's usable. Meals
    draw from the earliest-expiring lot first. Lots that pass their day are waste.
    Made ingredients (cooked beans from dried) are cooked in whole batches: what
    the meal doesn't use becomes a new lot that keeps for made_from.keeps_days.

Leftovers:
    A batch recipe (a loaf) makes recipe.serves portions however many are eating;
    anything else can be cooked for more people than are eating. Portions left
    over keep for recipe.keeps_days and can be eaten at a later meal of a type the
    recipe suits. Portions that pass their day are waste, like any other lot.

Flow:
    parse_inventory(inventory)      -> list[Lot]
    expire(lots, day)               -> lots still good + grams wasted
    consume(lots, consumed)         -> lots after a meal
    plan_week(recipes, inventory)   -> MealPlan
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import cast

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.models import Ingredient
from garde.models import Recipe
from garde.resolver import RecipeScore
from garde.resolver import Resolution
from garde.resolver import rank_recipes
from garde.vibes import Feedback


# Ingredients expiring within this many days get an urgency boost
URGENCY_HORIZON = 7

# {ingredient_id: 3 | "500g" | {"amount": ..., "expires": days} | [several of those]}
InventoryEntry = float | str | Mapping[str, float | str]
InventoryInput = Mapping[str, InventoryEntry | Sequence[InventoryEntry]]

# A time limit for every day, or one per day (index 0 = day 1); None = no limit
MinutesLimit = int | None | Sequence[int | None]


def limit_for(max_minutes: MinutesLimit, day: int) -> int | None:
    """The time limit on plan day `day` (1-based)."""
    if max_minutes is None or isinstance(max_minutes, int):
        return max_minutes
    return max_minutes[day - 1] if day - 1 < len(max_minutes) else None


# ---------------------------------------------------------------------------
# Stock: lots, parsing, expiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lot:
    ingredient_id: str
    grams: float
    expires_day: int | None = None  # last plan day it's usable; None = keeps


_AMOUNT = re.compile(r"^\s*([\d.]+)\s*(g|kg|ml|l)?\s*$", re.IGNORECASE)
_UNIT_GRAMS = {None: None, "g": 1.0, "ml": 1.0, "kg": 1000.0, "l": 1000.0}


def parse_amount(ing: Ingredient, value: float | str) -> float:
    """Grams from a natural-unit count (3) or a measured string ("500g", "1.5kg")."""
    if isinstance(value, str):
        m = _AMOUNT.match(value)
        if not m:
            raise ValueError(f"{ing.id}: can't parse amount {value!r}")
        number, unit = float(m.group(1)), m.group(2)
        factor = _UNIT_GRAMS[unit.lower() if unit else None]
        if factor is not None:
            return number * factor
        value = number
    if ing.unit_weight is None:
        raise ValueError(
            f"{ing.id}: no unit_weight, give a measured amount like '500g' instead of {value}"
        )
    return float(value) * ing.unit_weight


def parse_inventory(
    inventory: InventoryInput,
    ingredient_db: dict[str, Ingredient] | None = None,
) -> list[Lot]:
    db = ingredient_db or INGREDIENTS
    lots = []
    for ing_id, value in inventory.items():
        ing = db.get(ing_id)
        if ing is None:
            raise KeyError(f"unknown ingredient {ing_id!r}")
        entries = value if isinstance(value, list | tuple) else [value]
        lots.extend(_lot(ing, cast(InventoryEntry, entry)) for entry in entries)
    return lots


def _lot(ing: Ingredient, value: InventoryEntry) -> Lot:
    expires = ing.shelf_life_days
    if isinstance(value, (int, float, str)):
        amount = value
    else:
        amount = value["amount"]
        if value.get("expires") is not None:
            expires = int(value["expires"])
    return Lot(ing.id, parse_amount(ing, amount), expires)


def totals(lots: list[Lot]) -> dict[str, float]:
    out: dict[str, float] = {}
    for lot in lots:
        out[lot.ingredient_id] = out.get(lot.ingredient_id, 0.0) + lot.grams
    return out


def expire(lots: list[Lot], day: int) -> tuple[list[Lot], Counter[str]]:
    """Drop lots whose last usable day is before `day`. Returns (kept, grams wasted)."""
    kept, wasted = [], Counter()
    for lot in lots:
        if lot.expires_day is not None and lot.expires_day < day:
            wasted[lot.ingredient_id] += lot.grams
        else:
            kept.append(lot)
    return kept, wasted


def urgency(lots: list[Lot], day: int) -> dict[str, float]:
    """0..1 per ingredient: 1 if its soonest lot goes off today, 0 if a week+ away."""
    out: dict[str, float] = {}
    for lot in lots:
        if lot.expires_day is None or lot.grams <= 0:
            continue
        days_left = lot.expires_day - day
        u = max(0.0, 1.0 - days_left / URGENCY_HORIZON)
        out[lot.ingredient_id] = max(out.get(lot.ingredient_id, 0.0), u)
    return out


def consume(lots: list[Lot], consumed: Counter[str]) -> list[Lot]:
    """Take consumed grams from each ingredient's lots, soonest-expiring first."""
    remaining: dict[str, float] = dict(consumed)
    order = sorted(
        range(len(lots)),
        key=lambda i: math.inf if lots[i].expires_day is None else lots[i].expires_day,
    )
    out = list(lots)
    for i in order:
        lot = out[i]
        want = remaining.get(lot.ingredient_id, 0.0)
        if want <= 0:
            continue
        take = min(want, lot.grams)
        remaining[lot.ingredient_id] = want - take
        out[i] = replace(lot, grams=lot.grams - take)
    return [lot for lot in out if lot.grams > 1e-6]


def deplete(stock: dict[str, float], resolution: Resolution) -> dict[str, float]:
    """Grams left after a meal. Ingredients not in `stock` (unlimited) are ignored."""
    updated = dict(stock)
    for ing_id, grams in resolution.consumed.items():
        if ing_id in updated:
            updated[ing_id] = max(0.0, updated[ing_id] - grams)
    return updated


def cook_batches(
    lots: list[Lot], res: Resolution, day: int, db: dict[str, Ingredient]
) -> tuple[list[Lot], Counter[str]]:
    """
    After a meal: made ingredients are cooked a whole batch at a time, so round
    each up to its batch size and keep the rest as a lot of its own.
    Returns (lots, grams made in all, for the meal and the batch).
    """
    made = Counter(res.made)
    for raw, grams in res.made.items():
        made_from = db[raw].made_from
        if made_from is None or made_from.batch is None:
            continue
        batches = math.ceil(grams / made_from.batch - 1e-9)
        source_left = totals(lots).get(made_from.ingredient, 0.0)
        extra = min(batches * made_from.batch - grams, source_left / made_from.ratio)
        if extra < db[raw].crumb:
            continue
        lots = consume(lots, Counter({made_from.ingredient: extra * made_from.ratio}))
        keeps = made_from.keeps_days
        lots.append(Lot(raw, extra, None if keeps is None else day + keeps))
        made[raw] += extra
    return lots, made


# ---------------------------------------------------------------------------
# Leftovers: cooked dishes waiting to be eaten
# ---------------------------------------------------------------------------


def leftover_key(recipe_id: str) -> str:
    """How wasted leftovers are keyed in MealPlan.wasted, beside ingredient ids."""
    return f"leftovers:{recipe_id}"


@dataclass(frozen=True)
class Leftover:
    recipe_id: str
    portions: float
    grams_per_portion: float  # stocked grams that went into each portion
    cooked_day: int
    expires_day: int  # last day it's good
    meal_types: tuple[str, ...]
    resolution: Resolution  # what was cooked, for showing the meal again


def cook_servings(recipe: Recipe, people: int, times: int = 1) -> int:
    """Portions to cook: a whole batch, or `times` helpings for each person."""
    return recipe.serves if recipe.batch else people * times


def stash(
    dishes: list[Leftover], recipe: Recipe, res: Resolution, people: int, day: int
) -> tuple[list[Leftover], Counter[str]]:
    """Keep the portions nobody ate. Returns (dishes, grams wasted right away)."""
    spare = res.servings - people
    if spare <= 1e-9:
        return dishes, Counter()
    per_portion = sum(res.consumed.values()) / res.servings
    if recipe.keeps_days <= 0:
        return dishes, Counter({leftover_key(recipe.id): spare * per_portion})
    dish = Leftover(
        recipe_id=recipe.id,
        portions=spare,
        grams_per_portion=per_portion,
        cooked_day=day,
        expires_day=day + recipe.keeps_days,
        meal_types=tuple(recipe.meal_type),
        resolution=res,
    )
    return [*dishes, dish], Counter()


def expire_dishes(
    dishes: list[Leftover], day: int
) -> tuple[list[Leftover], Counter[str]]:
    """Throw out leftovers past their day. Returns (kept, grams wasted)."""
    kept, wasted = [], Counter()
    for d in dishes:
        if d.expires_day < day:
            wasted[leftover_key(d.recipe_id)] += d.portions * d.grams_per_portion
        else:
            kept.append(d)
    return kept, wasted


def eat_leftovers(
    dishes: list[Leftover], meal: str, people: int
) -> tuple[list[Leftover], Leftover | None]:
    """Serve the soonest-expiring leftovers that suit this meal and stretch to everyone."""
    fits = [
        i
        for i, d in enumerate(dishes)
        if meal in d.meal_types and d.portions >= people - 1e-9
    ]
    if not fits:
        return dishes, None
    i = min(fits, key=lambda i: dishes[i].expires_day)
    dish = dishes[i]
    rest = replace(dish, portions=dish.portions - people)
    out = dishes[:i] + ([rest] if rest.portions > 1e-9 else []) + dishes[i + 1 :]
    return out, dish


# ---------------------------------------------------------------------------
# Variety constraint helpers
# ---------------------------------------------------------------------------


def classes_used_recently(
    slots: list[MealSlot],
    current_day: int,
    gap: int,
    ingredient_db: dict[str, Ingredient] | None = None,
) -> set[str]:
    """
    Return the set of ingredient classes used in the last `gap` days.
    Used to penalise recipes that repeat the same class too soon.
    """
    db = ingredient_db or INGREDIENTS
    recent: set[str] = set()
    for slot in slots:
        if slot.resolution is None:
            continue
        if current_day - slot.day > gap:
            continue
        for assignment in slot.resolution.assignments:
            for ing_id in assignment.assigned:
                ing = db.get(ing_id)
                if ing:
                    recent.update(ing.classes)
    return recent


def variety_score(
    resolution: Resolution,
    recent_classes: set[str],
    ingredient_db: dict[str, Ingredient] | None = None,
) -> float:
    """
    Score a resolution by how much it avoids recently used ingredient classes.
    0.0 = total overlap, 1.0 = no overlap.
    """
    db = ingredient_db or INGREDIENTS
    recipe_classes: set[str] = set()
    for assignment in resolution.assignments:
        for ing_id in assignment.assigned:
            ing = db.get(ing_id)
            if ing:
                recipe_classes.update(ing.classes)
    if not recipe_classes:
        return 1.0
    overlap = len(recipe_classes & recent_classes) / len(recipe_classes)
    return 1.0 - overlap


def rescue_score(resolution: Resolution, lots: list[Lot], day: int) -> float:
    """
    Fraction of the at-risk stock (expiring within URGENCY_HORIZON), weighted by
    urgency, that this meal would use up. 0 if nothing is at risk.
    """
    urg = urgency(lots, day)
    at_risk = {i: g for i, g in totals(lots).items() if urg.get(i, 0) > 0}
    total = sum(urg[i] * g for i, g in at_risk.items())
    if total == 0:
        return 0.0
    saved = sum(
        urg[i] * min(resolution.consumed.get(i, 0.0), g) for i, g in at_risk.items()
    )
    return saved / total


# ---------------------------------------------------------------------------
# Meal plan models
# ---------------------------------------------------------------------------


@dataclass
class MealSlot:
    day: int
    meal: str
    recipe_id: str | None = None
    resolution: Resolution | None = None
    leftovers_from: int | None = None  # day it was cooked, if this is leftovers

    @property
    def filled(self) -> bool:
        return self.resolution is not None and self.resolution.success


class _Colours:
    """ANSI codes for the terminal summary, or blanks."""

    def __init__(self, on: bool) -> None:
        def code(n: str) -> str:
            return f"\033[{n}m" if on else ""

        self.cyan, self.green, self.yellow = code("36"), code("32"), code("33")
        self.red, self.bold, self.reset = code("31"), code("1"), code("0")


def _slot_lines(slot: MealSlot, c: _Colours) -> list[str]:
    """One meal in the terminal summary."""
    tag = f"  [{slot.meal.upper()}]"
    r = slot.resolution
    if not slot.filled or r is None:
        return [f"{tag} {c.red}✗ unplanned{c.reset}"]
    name = f"{tag} {c.bold}{c.green}{r.recipe_name}{c.reset}"
    if slot.leftovers_from is not None:
        return [f"{name}  (leftovers from day {slot.leftovers_from})"]
    lines = [
        f"{name}  (serves {r.servings}, {r.timing.total} min,"
        f" {r.timing.active} hands-on)"
    ]
    if r.timing.make_ahead:
        lines.append(
            f"    {c.cyan}⏰ make ahead: {', '.join(r.timing.make_ahead)}{c.reset}"
        )
    for a in r.assignments:
        if not a.assigned:
            continue
        picks = ", ".join(
            f"{i} {a.grams[i]:.0f}g" if a.grams.get(i) else i for i in a.assigned
        )
        lines.append(f"    • {a.slot_label + ':':<28} {c.yellow}{picks}{c.reset}")
    return lines


@dataclass
class MealPlan:
    days: int
    meals_per_day: list[str]
    slots: list[MealSlot]
    inventory_start: dict[str, float]  # grams
    inventory_remaining: dict[str, float]  # grams
    unplanned: list[tuple[int, str]] = field(default_factory=list)
    wasted: Counter[str] = field(default_factory=Counter)  # grams expired unused
    remaining_lots: list[Lot] = field(default_factory=list)
    bought: Counter[str] = field(default_factory=Counter)  # packs, from one shop
    leftovers: list[Leftover] = field(default_factory=list)  # still good at the end
    made: Counter[str] = field(default_factory=Counter)  # grams cooked from dried etc.

    @property
    def success_rate(self) -> float:
        filled = sum(1 for s in self.slots if s.filled)
        return filled / len(self.slots) if self.slots else 0.0

    @property
    def waste_fraction(self) -> float:
        start = sum(self.inventory_start.values())
        return sum(self.wasted.values()) / start if start else 0.0

    def summary(self, use_color: bool = True) -> str:
        c = _Colours(use_color)
        filled_count = sum(1 for s in self.slots if s.filled)
        rule = f"{c.bold}{'=' * 50}{c.reset}"
        all_done = filled_count == len(self.slots)
        lines = [
            rule,
            f"{c.bold}📅 Meal Plan — {self.days} days x {self.meals_per_day}{c.reset}",
            f"📊 Planned: {c.green if all_done else c.yellow}"
            f"{filled_count}/{len(self.slots)} meals{c.reset}",
            rule,
            "",
        ]
        for day in range(1, self.days + 1):
            lines.append(f"{c.cyan}{c.bold}♦ Day {day}{c.reset}")
            day_slots = [s for s in self.slots if s.day == day]
            if not day_slots:
                lines.append("  (No meals scheduled)")
            for slot in day_slots:
                lines += _slot_lines(slot, c)
            lines.append("")

        lines += [rule, f"{c.bold}📦 Stock{c.reset}", rule]
        for ing_id in sorted(self.inventory_start):
            start = self.inventory_start[ing_id]
            left = self.inventory_remaining.get(ing_id, 0.0)
            wasted = self.wasted.get(ing_id, 0.0)
            used = start - left - wasted
            colour = c.red if wasted else c.reset
            note = f"  {c.red}wasted {wasted:.0f}g{c.reset}" if wasted else ""
            lines.append(
                f"  {ing_id:<22} {colour}{left:>6.0f}g left{c.reset}  (used {used:.0f}g){note}"
            )
        for key, grams in sorted(self.wasted.items()):
            if key.startswith("leftovers:"):
                lines.append(
                    f"  {c.red}leftover {key.split(':', 1)[1]} went off: {grams:.0f}g{c.reset}"
                )
        lines += [
            "",
            f"{c.bold}🗑  Waste: {sum(self.wasted.values()):.0f}g "
            f"({self.waste_fraction:.0%} of starting stock){c.reset}",
        ]
        return "\n".join(lines)

    def shopping_items(
        self, ingredient_db: dict[str, Ingredient] | None = None
    ) -> list[dict]:
        """One row per thing to buy: id, name, packs, grams, price level."""
        db = ingredient_db or INGREDIENTS
        rows = []
        for ing_id, packs in sorted(self.bought.items(), key=lambda kv: db[kv[0]].name):
            ing = db[ing_id]
            rows.append(
                {
                    "id": ing_id,
                    "name": ing.name,
                    "packs": packs,
                    "grams": round(packs * ing.pack_grams),
                    "price": int(ing.price),
                    "price_word": ing.price.word,
                }
            )
        return rows

    def shopping_list(self, ingredient_db: dict[str, Ingredient] | None = None) -> str:
        rows = self.shopping_items(ingredient_db)
        if not rows:
            lines = ["Shopping list: nothing to buy"]
        else:
            spend = sum(r["price"] * r["packs"] for r in rows)
            lines = [f"Shopping list ({spend} price points):"]
            for r in rows:
                lines.append(
                    f"  {r['packs']} x {r['name']:<26} ({r['grams']}g)  {r['price_word']}"
                )
        for day, meal in self.unplanned:
            lines.append(f"  Day {day} {meal} — nothing to cook even with the shop")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def _pick(
    ranked: list[RecipeScore],
    used_recipes: set[str],
    planned: list[MealSlot],
    lots: list[Lot],
    day: int,
    gap: int,
    db: dict[str, Ingredient],
    jitter: float,
    rng: random.Random,
    max_minutes: int | None = None,
    feedback: Feedback | None = None,
) -> RecipeScore | None:
    viable = [
        rs
        for rs in ranked
        if rs.resolution.success
        and (max_minutes is None or rs.resolution.timing.total <= max_minutes)
    ]
    fresh = [rs for rs in viable if rs.recipe_id not in used_recipes]
    pool = fresh or viable  # allow repeats once every recipe has been used
    if not pool:
        return None
    recent = classes_used_recently(planned, day, gap, db)

    def combined(rs: RecipeScore) -> float:
        return (
            0.5 * variety_score(rs.resolution, recent, db)
            + 0.5 * rescue_score(rs.resolution, lots, day)
            + (0.3 * feedback.recipe_score(rs.recipe_id) if feedback else 0.0)
            + rng.uniform(0, jitter)
        )

    return max(pool, key=combined)


def plan_week(
    recipes: dict[str, Recipe] | None = None,
    inventory: InventoryInput | None = None,
    days: int = 7,
    meals_per_day: list[str] | None = None,
    people: int = 2,
    max_minutes: MinutesLimit = None,
    max_same_class_gap: int = 2,
    ingredient_db: dict[str, Ingredient] | None = None,
    randomise: bool = True,
    rng: random.Random | None = None,
    feedback: Feedback | None = None,
    leftovers: Sequence[Leftover] = (),
) -> MealPlan:
    """
    Plan meals across `days` days given an inventory. Greedy: one meal at a time.

    For each day:
        1. Throw out lots that have expired (counted as waste)
        For each meal:
            2. Resolve every recipe suitable for that meal against current stock,
               for `people` servings, with urgency from expiry dates
            3. Pick the successful recipe that best rescues at-risk stock and
               avoids recently used ingredient classes (no repeats until all used)
            4. Draw its ingredients from the soonest-expiring lots
        Leftovers that suit the meal are eaten first, instead of cooking.

    Args:
        recipes:              recipe db, defaults to RECIPES
        inventory:            see parse_inventory
        days:                 number of days to plan
        meals_per_day:        list of meal names, e.g. ["lunch", "dinner"]
        people:               servings per meal
        max_minutes:          skip recipes that take longer than this, start to plate
                              (one number, or a list with one per day)
        max_same_class_gap:   avoid repeating ingredient class within N days
        ingredient_db:        ingredient db, defaults to INGREDIENTS
        randomise:            jitter scores slightly for variety between runs
        rng:                  random source, pass a seeded Random for repeatable plans
        feedback:             your ratings (garde.vibes) to steer recipes and ingredients
        leftovers:            cooked dishes already in the fridge (garde.pantry.to_leftovers)
    """
    recipe_db = recipes or RECIPES
    db = ingredient_db or INGREDIENTS
    meals = meals_per_day or ["dinner"]
    rng = rng or random.Random()
    jitter = 0.05 if randomise else 0.0

    lots = parse_inventory(inventory or {}, db)
    inventory_start = totals(lots)
    planned: list[MealSlot] = []
    unplanned: list[tuple[int, str]] = []
    used_recipes: set[str] = set()
    wasted: Counter[str] = Counter()
    dishes: list[Leftover] = list(leftovers)
    made: Counter[str] = Counter()

    for day in range(1, days + 1):
        lots, gone = expire(lots, day)
        wasted += gone
        dishes, gone = expire_dishes(dishes, day)
        wasted += gone
        for meal in meals:
            dishes, dish = eat_leftovers(dishes, meal, people)
            if dish is not None:
                planned.append(
                    MealSlot(
                        day=day,
                        meal=meal,
                        recipe_id=dish.recipe_id,
                        resolution=dish.resolution,
                        leftovers_from=dish.cooked_day,
                    )
                )
                continue
            meal_recipes = {
                rid: r for rid, r in recipe_db.items() if meal in r.meal_type
            }
            ranked = rank_recipes(
                meal_recipes,
                totals(lots),
                db,
                rng=rng,
                servings=people,
                urgency=urgency(lots, day),
                feedback=feedback,
            )
            best = _pick(
                ranked,
                used_recipes,
                planned,
                lots,
                day,
                max_same_class_gap,
                db,
                jitter,
                rng,
                limit_for(max_minutes, day),
                feedback,
            )
            if best is None:
                planned.append(MealSlot(day=day, meal=meal))
                unplanned.append((day, meal))
                continue
            planned.append(
                MealSlot(
                    day=day,
                    meal=meal,
                    recipe_id=best.recipe_id,
                    resolution=best.resolution,
                )
            )
            used_recipes.add(best.recipe_id)
            lots = consume(lots, best.resolution.consumed)
            lots, cooked = cook_batches(lots, best.resolution, day, db)
            made += cooked
            dishes, gone = stash(
                dishes, recipe_db[best.recipe_id], best.resolution, people, day
            )
            wasted += gone

    # Lots whose last good day fell inside the plan and went uneaten are waste too
    lots, gone = expire(lots, days + 1)
    wasted += gone
    dishes, gone = expire_dishes(dishes, days + 1)
    wasted += gone

    return MealPlan(
        days=days,
        meals_per_day=meals,
        slots=planned,
        inventory_start=inventory_start,
        inventory_remaining=totals(lots),
        unplanned=unplanned,
        wasted=wasted,
        remaining_lots=lots,
        leftovers=dishes,
        made=made,
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo_inventory: InventoryInput = {
        "spinach": {"amount": 1, "expires": 2},  # a bag on its last legs
        "kale": {"amount": 1, "expires": 3},
        "carrot": 8,
        "cauliflower": 1,
        "tomato": 3,
        "coriander_leaf": 1,
        "potato": 4,
        "red_lentils": "500g",
        "coconut_milk": 2,
        "cannellini_beans": 2,
        "cashews": "200g",
        "mushrooms": 1,
        "sun_dried_tomatoes": 1,
        "frozen_spinach": "900g",
        "vegan_parmesan": "150g",
        "green_onion": 4,
    }

    plan = plan_week(inventory=demo_inventory, days=7, meals_per_day=["dinner"])
    print(plan.summary())
    print()
    print(plan.shopping_list())
