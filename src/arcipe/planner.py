"""
arcipe.planner
==============
Week-level meal planner. Resolves recipes across multiple days,
depleting a shared inventory pool and enforcing variety constraints.

Inventory model:
    dict[str, int] — ingredient_id -> portion count
    UNLIMITED = 100 — pantry staples that never run out

Flow:
    build_pool(inventory)           -> list[str] for resolver
    deplete(inventory, resolution)  -> updated inventory
    plan_week(recipes, inventory)   -> MealPlan
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from dataclasses import field

from arcipe.models import Ingredient
from arcipe.models import Recipe
from arcipe.models import Season
from arcipe.ontology import INGREDIENTS
from arcipe.ontology import RECIPES
from arcipe.resolver import CURRENT_SEASON
from arcipe.resolver import Resolution
from arcipe.resolver import rank_recipes
from arcipe.resolver import resolve


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNLIMITED = 100


# ---------------------------------------------------------------------------
# Inventory helpers
# ---------------------------------------------------------------------------


def build_pool(inventory: dict[str, int]) -> list[str]:
    """
    Expand dict inventory into a flat portion pool for the resolver.
    {"carrots": 3, "spinach": 1} -> ["carrots", "carrots", "carrots", "spinach"]
    """
    pool = []
    for ing_id, portions in inventory.items():
        pool.extend([ing_id] * portions)
    return pool


def deplete(
    inventory: dict[str, int],
    resolution: Resolution,
) -> dict[str, int]:
    """
    Return a new inventory dict with portions decremented for everything
    used in the resolution. UNLIMITED ingredients (== 100) are not decremented
    below UNLIMITED — they replenish to UNLIMITED each time.
    Raises nothing on over-depletion; floors at 0.
    """
    updated = dict(inventory)
    for assignment in resolution.assignments:
        for ing_id in assignment.assigned:
            if ing_id not in updated:
                continue
            if updated[ing_id] >= UNLIMITED:
                continue  # pantry staple — leave it alone
            updated[ing_id] = max(0, updated[ing_id] - 1)
    return updated


def perishables_first(
    inventory: dict[str, int],
    ingredient_db: dict[str, Ingredient] | None = None,
) -> list[str]:
    """
    Return ingredient ids sorted so perishables appear first in the pool.
    Perishables with low remaining portions get priority.
    """
    db = ingredient_db or INGREDIENTS
    perishable = []
    stable = []
    for ing_id, portions in inventory.items():
        ing = db.get(ing_id)
        if ing is None:
            continue
        if getattr(ing, "perishable", False) and portions < UNLIMITED:
            perishable.append((ing_id, portions))
        else:
            stable.append(ing_id)
    # Sort perishables by portions ascending — lowest stock first
    perishable.sort(key=lambda x: x[1])
    return [ing_id for ing_id, _ in perishable] + stable


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
    inventory_start: dict[str, int]
    inventory_remaining: dict[str, int]
    unplanned: list[tuple[int, str]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        filled = sum(1 for s in self.slots if s.filled)
        return filled / len(self.slots) if self.slots else 0.0

    def summary(self) -> str:
        lines = [
            f"Meal Plan — {self.days} days x {self.meals_per_day}",
            f"Planned: {sum(1 for s in self.slots if s.filled)}/{len(self.slots)} meals",
            "",
        ]
        for day in range(1, self.days + 1):
            lines.append(f"Day {day}")
            for slot in self.slots:
                if slot.day != day:
                    continue
                if slot.filled and slot.resolution:
                    r = slot.resolution
                    assignments = ", ".join(
                        f"{a.slot_label}: {', '.join(a.assigned)}"
                        for a in r.assignments
                        if a.assigned
                    )
                    lines.append(f"  [{slot.meal}] {r.recipe_name}")
                    lines.append(f"    {assignments}")
                else:
                    lines.append(f"  [{slot.meal}] ✗ unplanned")
        lines.append("")
        lines.append("Inventory remaining:")
        for ing_id, portions in sorted(self.inventory_remaining.items()):
            if portions >= UNLIMITED:
                continue  # don't clutter output with unlimited staples
            start = self.inventory_start.get(ing_id, 0)
            used = start - portions
            lines.append(f"  {ing_id:<25} {portions:>2} remaining  (used {used})")
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


def plan_week(
    recipes: dict[str, Recipe] | None = None,
    inventory: dict[str, int] | None = None,
    days: int = 7,
    meals_per_day: list[str] | None = None,
    max_same_class_gap: int = 2,
    season: Season = CURRENT_SEASON,
    ingredient_db: dict[str, Ingredient] | None = None,
    randomise: bool = True,
) -> MealPlan:
    """
    Plan meals across `days` days given an inventory.

    For each meal slot:
        1. Build pool from current inventory (perishables first)
        2. Rank all recipes by resolver score against current pool
        3. Apply variety penalty for recently used ingredient classes
        4. Pick the best scoring recipe not already used this week
        5. Deplete inventory
        6. Record slot

    Args:
        recipes:              recipe db, defaults to RECIPES
        inventory:            dict[ingredient_id, portions], defaults to empty
        days:                 number of days to plan
        meals_per_day:        list of meal names, e.g. ["lunch", "dinner"]
        max_same_class_gap:   avoid repeating ingredient class within N days
        season:               current season for scoring
        ingredient_db:        ingredient db, defaults to INGREDIENTS
        randomise:            shuffle equal-scored recipes for variety
    """
    recipe_db = recipes or RECIPES
    db = ingredient_db or INGREDIENTS
    inv = dict(inventory or {})
    meals = meals_per_day or ["dinner"]

    planned_slots: list[MealSlot] = []
    unplanned: list[tuple[int, str]] = []
    used_recipes: set[str] = set()
    inventory_start = dict(inv)

    for day in range(1, days + 1):
        for meal in meals:
            # Build pool — perishables sorted to front
            ordered = perishables_first(inv, db)
            pool = build_pool({ing: inv[ing] for ing in ordered if ing in inv})

            # Rank recipes
            ranked = rank_recipes(recipe_db, pool, db)

            # Filter already used this week
            candidates = [rs for rs in ranked if rs.recipe_id not in used_recipes]

            if not candidates:
                # All recipes exhausted — allow repeats
                candidates = ranked[:]

            # Apply variety penalty
            recent = classes_used_recently(planned_slots, day, max_same_class_gap, db)

            def combined_score(rs, _recent: set[str] = recent) -> float:
                v = variety_score(rs.resolution, _recent, db)
                base = rs.score * 0.7 + v * 0.3
                if randomise:
                    base += random.uniform(0, 0.05)
                return base

            candidates.sort(key=combined_score, reverse=True)

            best = candidates[0] if candidates else None

            if best is None or not best.resolution.success:
                slot = MealSlot(day=day, meal=meal)
                unplanned.append((day, meal))
            else:
                # Re-resolve with current pool to get accurate assignment
                final = resolve(recipe_db[best.recipe_id], pool, db, season)
                slot = MealSlot(
                    day=day,
                    meal=meal,
                    recipe_id=best.recipe_id,
                    resolution=final,
                )
                used_recipes.add(best.recipe_id)
                inv = deplete(inv, final)

            planned_slots.append(slot)

    return MealPlan(
        days=days,
        meals_per_day=meals,
        slots=planned_slots,
        inventory_start=inventory_start,
        inventory_remaining=inv,
        unplanned=unplanned,
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from arcipe.ontology import RECIPES as REC_DB

    demo_inventory: dict[str, int] = {
        # Perishables — limited portions
        "spinach": 2,
        "carrot": 8,
        "cauliflower": 2,
        "tomato": 3,
        "coriander_leaf": 2,
        "potato": 4,
        # Proteins — a few cans/portions each
        "chickpeas": 4,
        "kidney_beans": 3,
        "lentils": 4,
        "white_beans": 2,
        # Grains
        "basmati_rice": UNLIMITED,
        "spaghetti": UNLIMITED,
        "flatbread": 3,
        # Pantry staples — unlimited
        "garlic": UNLIMITED,
        "onion": UNLIMITED,
        "ginger": UNLIMITED,
        "garam_masala": UNLIMITED,
        "cumin": UNLIMITED,
        "coriander_seed": UNLIMITED,
        "chilli_powder": UNLIMITED,
        "turmeric_dried": UNLIMITED,
        "curry_powder": UNLIMITED,
        "plain_flour": UNLIMITED,
        "olive_oil": UNLIMITED,
        "canned_tomato": UNLIMITED,
        "lemon": UNLIMITED,
        "nutritional_yeast": UNLIMITED,
        # Creamy options
        "cashews": 2,
        "coconut_milk": 3,
        "sunflower_seeds": UNLIMITED,
        # Seeds/nuts
        "pumpkin_seeds": UNLIMITED,
    }

    plan = plan_week(
        recipes=REC_DB,
        inventory=demo_inventory,
        days=7,
        meals_per_day=["dinner"],
        randomise=True,
    )

    print(plan.summary())
    print()
    print(plan.shopping_list())
