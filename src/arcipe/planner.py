"""
arcipe.planner
==============
Week-level meal planner. Resolves recipes across multiple days,
depleting a shared inventory pool and enforcing variety constraints.

Inventory model:
    dict[str, int] — ingredient_id -> portion count
    UNLIMITED = 100 — pantry staples that never run out

Flow:
    in_stock(inventory)             -> list[str] for resolver
    deplete(inventory, resolution)  -> updated inventory
    plan_week(recipes, inventory)   -> MealPlan

Perishables are prioritised by the resolver's scoring, not by pool order.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from dataclasses import field

from arcipe.loader import INGREDIENTS
from arcipe.loader import RECIPES
from arcipe.models import Ingredient
from arcipe.models import Recipe
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


def in_stock(inventory: dict[str, int]) -> list[str]:
    """Ingredient ids with at least one portion left. Unlimited staples are added by the resolver."""
    return [ing_id for ing_id, portions in inventory.items() if portions > 0]


def deplete(
    inventory: dict[str, int],
    resolution: Resolution,
) -> dict[str, int]:
    """
    Return a new inventory dict with portions decremented for every raw ingredient
    the resolution consumed. Prepared forms count against their bases, so cashew
    cream uses up cashews. UNLIMITED ingredients (>= 100) are left alone.
    Raises nothing on over-depletion; floors at 0.
    """
    updated = dict(inventory)
    for ing_id, portions in resolution.consumed.items():
        if ing_id not in updated:
            continue
        if updated[ing_id] >= UNLIMITED:
            continue  # pantry staple — leave it alone
        updated[ing_id] = max(0, updated[ing_id] - portions)
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

    def summary(self, use_color: bool = True) -> str:
        # Simple ANSI escape sequences for clean terminal aesthetics
        CYAN = "\033[36m" if use_color else ""
        GREEN = "\033[32m" if use_color else ""
        YELLOW = "\033[33m" if use_color else ""
        RED = "\033[31m" if use_color else ""
        BOLD = "\033[1m" if use_color else ""
        RESET = "\033[0m" if use_color else ""

        filled_count = sum(1 for s in self.slots if s.filled)

        lines = [
            f"{BOLD}=================================================={RESET}",
            f"{BOLD}📅 Meal Plan — {self.days} days x {self.meals_per_day}{RESET}",
            f"📊 Planned: {GREEN if filled_count == len(self.slots) else YELLOW}{filled_count}/{len(self.slots)} meals{RESET}",
            f"{BOLD}=================================================={RESET}",
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
                    )

                    # Instead of a single giant line, print each slot assignment as an indented sub-tree leaf
                    for a in r.assignments:
                        if not a.assigned:
                            continue
                        # Aligns the slot labels nicely to a 28-character width
                        label_str = f"{a.slot_label}:"
                        ingredients_str = ", ".join(a.assigned)
                        lines.append(
                            f"    • {label_str:<28} {YELLOW}{ingredients_str}{RESET}"
                        )
                else:
                    lines.append(f"  [{slot.meal.upper()}] {RED}✗ unplanned{RESET}")
            lines.append("")  # Spacer between days

        lines.append(f"{BOLD}=================================================={RESET}")
        lines.append(f"{BOLD}📦 Inventory Status{RESET}")
        lines.append(f"{BOLD}=================================================={RESET}")

        for ing_id, portions in sorted(self.inventory_remaining.items()):
            if portions >= UNLIMITED:
                continue
            start = self.inventory_start.get(ing_id, 0)
            used = start - portions

            # Highlight if you completely cleared out an item
            stock_color = RED if portions == 0 else RESET

            lines.append(
                f"  {ing_id:<25} -> {stock_color}{portions:>2} remaining{RESET}  (used {used})"
            )
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
    ingredient_db: dict[str, Ingredient] | None = None,
    randomise: bool = True,
    rng: random.Random | None = None,
) -> MealPlan:
    """
    Plan meals across `days` days given an inventory.

    For each meal slot:
        1. Build pool from current inventory
        2. Rank recipes suitable for that meal (recipe.meal_type) against the pool
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
        ingredient_db:        ingredient db, defaults to INGREDIENTS
        randomise:            shuffle equal-scored recipes for variety
        rng:                  random source, pass a seeded Random for repeatable plans
    """
    recipe_db = recipes or RECIPES
    db = ingredient_db or INGREDIENTS
    inv = dict(inventory or {})
    meals = meals_per_day or ["dinner"]
    rng = rng or random.Random()

    planned_slots: list[MealSlot] = []
    unplanned: list[tuple[int, str]] = []
    used_recipes: set[str] = set()
    inventory_start = dict(inv)

    for day in range(1, days + 1):
        for meal in meals:
            pool = in_stock(inv)
            meal_recipes = {
                rid: r for rid, r in recipe_db.items() if meal in r.meal_type
            }
            ranked = rank_recipes(meal_recipes, pool, db, rng=rng)

            # Filter already used this week
            candidates = [
                rs
                for rs in ranked
                if rs.recipe_id not in used_recipes and rs.resolution.success
            ]

            if not candidates:
                # All recipes exhausted — allow repeats
                candidates = ranked[:]

            # Apply variety penalty
            recent = classes_used_recently(planned_slots, day, max_same_class_gap, db)

            def combined_score(rs, _recent: set[str] = recent) -> float:
                v = variety_score(rs.resolution, _recent, db)
                base = rs.score * 0.7 + v * 0.3
                if randomise:
                    base += rng.uniform(0, 0.05)
                return base

            candidates.sort(key=combined_score, reverse=True)

            best = candidates[0] if candidates else None

            if best is None or not best.resolution.success:
                slot = MealSlot(day=day, meal=meal)
                unplanned.append((day, meal))
            else:
                # Re-resolve with current pool to get accurate assignment
                final = resolve(recipe_db[best.recipe_id], pool, db, rng=rng)
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
    from arcipe.loader import RECIPES as REC_DB

    demo_inventory: dict[str, int] = {
        # --- Your Existing Inventory ---
        "spinach": 2,
        "carrot": 8,
        "cauliflower": 2,
        "tomato": 3,
        "coriander_leaf": 2,
        "potato": 4,
        "red_lentils": 4,
        "coconut_milk": 3,
        # --- Add These to Unlock Creamy Tuscan Beans ---
        "cannellini_beans": 2,  # Satisfies stew_protein_legume
        "cashews": 3,  # Unlocks thickened_cashew_cream
        "mushrooms": 2,  # Satisfies stew_bulk_umami
        "sun_dried_tomatoes": 1,  # Satisfies stew_bulk_umami
        "frozen_spinach": 2,  # Satisfies stew_wilted_greens
        "vegan_parmesan": 1,  # Satisfies stew_meltable_finisher
        "green_onion": 1,  # Optional/Extra for stew_aromatic_base
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
