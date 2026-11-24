"""
arcipe.planner
==============
Week-level meal planner. Resolves recipes across multiple days, drawing down a
shared stock of ingredient lots and enforcing variety constraints.

Inventory input (see parse_inventory):
    {"carrot": 6}                              — natural units (Ingredient.unit_weight)
    {"red_lentils": "500g"}                    — grams / kg / ml / l
    {"spinach": {"amount": 1, "expires": 2}}   — explicit days until it goes off
Fresh things default to their Ingredient.shelf_life_days. Unlimited staples are
left out of the inventory entirely; listing one makes it tracked.

Stock model:
    A Lot is some grams of one ingredient with the last day it's usable. Meals
    draw from the earliest-expiring lot first. Lots that pass their day are waste.

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
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import cast

from arcipe.loader import INGREDIENTS
from arcipe.loader import RECIPES
from arcipe.models import Ingredient
from arcipe.models import Recipe
from arcipe.resolver import RecipeScore
from arcipe.resolver import Resolution
from arcipe.resolver import rank_recipes
from arcipe.vibes import Feedback


# Ingredients expiring within this many days get an urgency boost
URGENCY_HORIZON = 7

# {ingredient_id: 3 | "500g" | {"amount": ..., "expires": days}}
InventoryInput = Mapping[str, float | str | Mapping[str, float | str]]


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
        expires = ing.shelf_life_days
        if isinstance(value, (int, float, str)):
            amount = value
        else:
            entry = cast(Mapping[str, float | str], value)
            amount = entry["amount"]
            if "expires" in entry:
                expires = int(entry["expires"])
        lots.append(Lot(ing_id, parse_amount(ing, amount), expires))
    return lots


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

    @property
    def filled(self) -> bool:
        return self.resolution is not None and self.resolution.success


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

    @property
    def success_rate(self) -> float:
        filled = sum(1 for s in self.slots if s.filled)
        return filled / len(self.slots) if self.slots else 0.0

    @property
    def waste_fraction(self) -> float:
        start = sum(self.inventory_start.values())
        return sum(self.wasted.values()) / start if start else 0.0

    def summary(self, use_color: bool = True) -> str:
        # Simple ANSI escape sequences for clean terminal aesthetics
        CYAN = "\033[36m" if use_color else ""
        GREEN = "\033[32m" if use_color else ""
        YELLOW = "\033[33m" if use_color else ""
        RED = "\033[31m" if use_color else ""
        BOLD = "\033[1m" if use_color else ""
        RESET = "\033[0m" if use_color else ""

        filled_count = sum(1 for s in self.slots if s.filled)
        rule = f"{BOLD}{'=' * 50}{RESET}"

        lines = [
            rule,
            f"{BOLD}📅 Meal Plan — {self.days} days x {self.meals_per_day}{RESET}",
            f"📊 Planned: {GREEN if filled_count == len(self.slots) else YELLOW}{filled_count}/{len(self.slots)} meals{RESET}",
            rule,
            "",
        ]

        for day in range(1, self.days + 1):
            lines.append(f"{CYAN}{BOLD}♦ Day {day}{RESET}")

            day_slots = [s for s in self.slots if s.day == day]
            if not day_slots:
                lines.append("  (No meals scheduled)")
                continue

            for slot in day_slots:
                if slot.filled and slot.resolution:
                    r = slot.resolution
                    lines.append(
                        f"  [{slot.meal.upper()}] {BOLD}{GREEN}{r.recipe_name}{RESET}"
                        f"  (serves {r.servings}, {r.timing.total} min,"
                        f" {r.timing.active} hands-on)"
                    )
                    if r.timing.make_ahead:
                        lines.append(
                            f"    {CYAN}⏰ make ahead: {', '.join(r.timing.make_ahead)}{RESET}"
                        )
                    for a in r.assignments:
                        if not a.assigned:
                            continue
                        label_str = f"{a.slot_label}:"
                        ingredients_str = ", ".join(
                            f"{i} {a.grams[i]:.0f}g" if a.grams.get(i) else i
                            for i in a.assigned
                        )
                        lines.append(
                            f"    • {label_str:<28} {YELLOW}{ingredients_str}{RESET}"
                        )
                else:
                    lines.append(f"  [{slot.meal.upper()}] {RED}✗ unplanned{RESET}")
            lines.append("")

        lines += [rule, f"{BOLD}📦 Stock{RESET}", rule]
        for ing_id in sorted(self.inventory_start):
            start = self.inventory_start[ing_id]
            left = self.inventory_remaining.get(ing_id, 0.0)
            wasted = self.wasted.get(ing_id, 0.0)
            used = start - left - wasted
            colour = RED if wasted else RESET
            note = f"  {RED}wasted {wasted:.0f}g{RESET}" if wasted else ""
            lines.append(
                f"  {ing_id:<22} {colour}{left:>6.0f}g left{RESET}  (used {used:.0f}g){note}"
            )
        lines += [
            "",
            f"{BOLD}🗑  Waste: {sum(self.wasted.values()):.0f}g "
            f"({self.waste_fraction:.0%} of starting stock){RESET}",
        ]
        return "\n".join(lines)

    def shopping_list(self) -> str:
        """
        Return ingredients needed that weren't in the starting inventory.
        Placeholder — needs gap analysis once we have target recipes.
        """
        lines = ["Shopping list: (gaps from unplanned slots)"]
        for day, meal in self.unplanned:
            lines.append(
                f"  Day {day} {meal} — could not be planned with current inventory"
            )
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
    max_minutes: int | None = None,
    max_same_class_gap: int = 2,
    ingredient_db: dict[str, Ingredient] | None = None,
    randomise: bool = True,
    rng: random.Random | None = None,
    feedback: Feedback | None = None,
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

    Args:
        recipes:              recipe db, defaults to RECIPES
        inventory:            see parse_inventory
        days:                 number of days to plan
        meals_per_day:        list of meal names, e.g. ["lunch", "dinner"]
        people:               servings per meal
        max_minutes:          skip recipes that take longer than this, start to plate
        max_same_class_gap:   avoid repeating ingredient class within N days
        ingredient_db:        ingredient db, defaults to INGREDIENTS
        randomise:            jitter scores slightly for variety between runs
        rng:                  random source, pass a seeded Random for repeatable plans
        feedback:             your ratings (arcipe.vibes) to steer recipes and ingredients
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

    for day in range(1, days + 1):
        lots, gone = expire(lots, day)
        wasted += gone
        for meal in meals:
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
                max_minutes,
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

    # Lots whose last good day fell inside the plan and went uneaten are waste too
    lots, gone = expire(lots, days + 1)
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
