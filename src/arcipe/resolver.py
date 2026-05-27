"""
Recipe Graph resolution
===============
Resolves abstract recipes into concrete ingredient assignments given an inventory.

Flow:
    candidates(slot, inventory) -> list of matching Ingredients
    score(ingredient, slot)     -> float
    resolve(recipe, inventory)  -> Resolution
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from itertools import groupby

from arcipe.models import COLOUR_MAX
from arcipe.models import CUISINE_FAMILY
from arcipe.models import ComponentSlot
from arcipe.models import Ingredient
from arcipe.models import PreparedIngredient
from arcipe.models import Recipe
from arcipe.models import Season
from arcipe.models import colour_distance
from arcipe.ontology import INGREDIENTS
from arcipe.ontology import PREPARED_INGREDIENTS
from arcipe.utils import get_season


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------


@dataclass
class SlotAssignment:
    slot_id: str
    slot_label: str
    assigned: list[str]  # ingredient ids, up to slot.max_ingredients
    candidates: list[str]  # all valid candidates before selection
    filled: bool  # False if required slot could not be filled


@dataclass
class Resolution:
    recipe_id: str
    recipe_name: str
    assignments: list[SlotAssignment]
    used: set[str]  # ingredient ids consumed across all slots
    unfillable: list[str]  # slot ids that could not be filled (required only)

    @property
    def success(self) -> bool:
        return len(self.unfillable) == 0

    def summary(self) -> str:
        lines = [f"[{self.recipe_name}] {'✓' if self.success else '✗'}"]
        for a in self.assignments:
            status = "✓" if a.filled else "✗ UNFILLABLE"
            assigned = ", ".join(a.assigned) if a.assigned else "—"
            lines.append(f"  {a.slot_label:<22} {status}  →  {assigned}")
        if not self.success:
            lines.append(f"\n  Unfillable slots: {', '.join(self.unfillable)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Candidate matching
# ---------------------------------------------------------------------------


def candidates(
    slot: ComponentSlot,
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
    min_coverage: float = 1.0,
) -> list[Ingredient]:
    """
    Return all ingredients from inventory that satisfy the slot's required_roles.
    min_coverage=1.0 enforces full role coverage (default).
    Lower values allow partial matches — useful for 'almost there' suggestions.
    """
    db = ingredient_db or INGREDIENTS
    result = []
    for ing_id in inventory:
        ing = db.get(ing_id)
        if ing is None:
            continue
        if slot.required_colour:
            dist = colour_distance(ing.colour, slot.required_colour)
            if dist / COLOUR_MAX > slot.colour_tolerance:
                continue
        required = set(slot.required_roles)
        covered = required & set(ing.roles)
        coverage = len(covered) / len(required) if required else 1.0
        if coverage >= min_coverage:
            result.append(ing)
    return result


def expand_inventory(
    inventory: list[str],
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> dict[str, Ingredient]:
    expanded = dict(ingredient_db)
    for prep in prepared_db.values():
        base_available = all(
            i in inventory or (ingredient_db.get(i) and ingredient_db[i].unlimited)
            for i in prep.base_ingredients
        )
        if not base_available:
            continue
        base = ingredient_db.get(prep.base_ingredients[0])
        if base is None:
            continue
        synthetic = Ingredient(
            id=prep.id,
            name=f"{prep.id.replace('_', ' ')} (prepared)",
            classes=base.classes,
            roles=prep.unlocked_roles,
            flavour=base.flavour,
            colour=prep.colour or base.colour,
            season=base.season,
            cost=base.cost,
            nutrition=base.nutrition,
            unlimited=all(
                ingredient_db[i].unlimited
                for i in prep.base_ingredients
                if i in ingredient_db
            ),
            cuisine_context=prep.cuisine_context,
            notes=prep.notes,
        )
        expanded[prep.id] = synthetic
    return expanded


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Current season — hardcoded for now, will come from context later
CURRENT_SEASON: Season = get_season(date.today())

COST_SCORE: dict[str, float] = {
    "very_low": 1.0,
    "low": 0.8,
    "medium": 0.5,
    "high": 0.2,
}


def score(
    ingredient: Ingredient, slot: ComponentSlot, preferred: list[str], recipe: Recipe
) -> float:
    """
    Score an ingredient against a slot. Higher is better.

    Components:
        preferred         — Preferential ingredients, higher score
        role_coverage     — what fraction of slot's required_roles the ingredient covers
                            (always 1.0 after candidate filtering, but useful if we relax later)
        preferred_class   — bonus if ingredient is in slot.preferred_classes
        preferred_role    — bonus if ingredient is also in slot.preferred_classes
        season            — bonus if ingredient is in season or available all year
        cost              — cheaper ingredients score higher (maximise use of cheap staples)
        role_depth        — ingredients with more roles are more versatile, slight bonus
        cuisine_context   — scores worse if outside of cuisine family
    """
    s = 0.0

    # Role coverage — always 1.0 post-filter but keep explicit
    required = set(slot.required_roles)
    covered = required & set(ingredient.roles)
    s += len(covered) / len(required) if required else 1.0

    # Preferred class bonus
    if any(c in slot.preferred_classes for c in ingredient.classes):
        s += 0.5

    # Preferred role bonus# preferred role bonus
    if any(r in slot.preferred_roles for r in ingredient.roles):
        s += 0.4

    # Season bonus
    if "all" in ingredient.season or CURRENT_SEASON in ingredient.season:
        s += 0.3
    elif "forage" in ingredient.season:
        s += 0.4  # forage ingredients are free, reward them

    # Cost score
    s += COST_SCORE.get(ingredient.cost, 0.5)

    # Role depth — slight bonus for polymorphic ingredients
    s += min(len(ingredient.roles) / 10, 0.2)

    # Bonus if ingredient is preferred
    if ingredient.id in preferred:
        s += 0.5

    if recipe.cuisine_context and ingredient.cuisine_context:
        recipe_families = {CUISINE_FAMILY[c] for c in recipe.cuisine_context}
        ing_families = {CUISINE_FAMILY[c] for c in ingredient.cuisine_context}
        if not recipe_families & ing_families:
            s -= 0.8  # heavy penalty, not hard exclude

    # Massive boost for this being a required ingredient
    if ingredient.id in recipe.required_ingredients:
        s += 10.0

    return s


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve(  # noqa: C901
    recipe: Recipe,
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
) -> Resolution:
    """
    Resolve a recipe against an inventory.

    For each slot (in order):
        1. Find candidates from remaining inventory
        2. Score and rank them
        3. Assign the top N (up to slot.max_ingredients), removing from pool
        4. If slot is required and unfillable, record it

    Returns a Resolution with full assignment detail.
    """
    db = ingredient_db or INGREDIENTS
    # Expand inventory with prepared forms
    expanded_db = expand_inventory(inventory, db, PREPARED_INGREDIENTS)
    # Also inject prepared ingredient ids into the pool if base is present
    expanded_inventory = list(inventory)

    # inject unlimited ingredients automatically — always available
    for ing_id, ing in db.items():
        if ing.unlimited and ing_id not in expanded_inventory:
            expanded_inventory.append(ing_id)

    for prep in PREPARED_INGREDIENTS.values():
        if (
            all(i in inventory for i in prep.base_ingredients)
            and prep.id not in expanded_inventory
        ):
            expanded_inventory.append(prep.id)

    # Required ingredients check
    for ing_id in recipe.required_ingredients:
        if ing_id not in inventory:
            return Resolution(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                assignments=[],
                used=set(),
                unfillable=["required_ingredient_missing"],
            )

    remaining = list(set(expanded_inventory))  # mutable pool
    assignments: list[SlotAssignment] = []
    unfillable: list[str] = []
    used: set[str] = set()
    preferred = recipe.preferred_ingredients

    for slot in recipe.slots:
        viable = candidates(slot, remaining, expanded_db)

        # Score and sort descending
        ranked = []
        viable_scored = sorted(
            viable,
            key=lambda ing: score(ing, slot, preferred, recipe),
            reverse=True,
        )
        for _, group in groupby(
            viable_scored,
            key=lambda ing: score(ing, slot, preferred, recipe),
        ):
            group_list = list(group)
            random.shuffle(group_list)
            ranked.extend(group_list)

        candidate_ids = [ing.id for ing in ranked]

        # Assign top N up to max_ingredients
        chosen = ranked[: slot.max_ingredients]
        chosen_ids = [ing.id for ing in chosen]

        # Remove assigned ingredients from pool (no-reuse)
        if recipe.no_ingredient_reuse:
            # After assigning chosen_ids, also remove base ingredients of any prepared forms
            for ing_id in chosen_ids:
                prep = PREPARED_INGREDIENTS.get(ing_id)
                if prep and all(i in remaining for i in prep.base_ingredients):
                    for ing in prep.base_ingredients:
                        remaining.remove(ing)
            used.update(chosen_ids)

        filled = bool(chosen) or slot.optional

        if not filled:
            unfillable.append(slot.id)

        assignments.append(
            SlotAssignment(
                slot_id=slot.id,
                slot_label=slot.label,
                assigned=chosen_ids,
                candidates=candidate_ids,
                filled=filled,
            )
        )

    return Resolution(
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        assignments=assignments,
        used=used,
        unfillable=unfillable,
    )


# ---------------------------------------------------------------------------
# Multi-recipe: score a list of recipes against inventory
# ---------------------------------------------------------------------------


@dataclass
class RecipeScore:
    recipe_id: str
    recipe_name: str
    resolution: Resolution
    score: float  # [0.0, 1.0], fraction of required slots filled


def rank_recipes(
    recipes: dict[str, Recipe],
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
) -> list[RecipeScore]:
    """
    Resolve all recipes against inventory and rank by how completely they can be filled.
    Useful for the meal planner: given what I have, what can I actually make?
    """
    results = []
    for recipe in recipes.values():
        r = resolve(recipe, inventory, ingredient_db)
        required_slots = [
            a for a in r.assignments if not _slot_is_optional(a.slot_id, recipe)
        ]
        filled = sum(1 for a in required_slots if a.filled)
        total = len(required_slots)
        recipe_score = 0.0 if "required_ingredient_missing" in r.unfillable else filled / total if total else 1.0
        results.append(
            RecipeScore(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                resolution=r,
                score=recipe_score,
            )
        )
    return sorted(results, key=lambda rs: rs.score, reverse=True)


def _slot_is_optional(slot_id: str, recipe: Recipe) -> bool:
    for slot in recipe.slots:
        if slot.id == slot_id:
            return slot.optional
    return False


# ---------------------------------------------------------------------------
# Quick demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from arcipe.ontology import RECIPES

    # We always got spices
    BASE_INVENTORY = [
        "garan_masala",
        "cumin",
        "coriander_seed",
        "chilli_powder",
        "smoked_paprika",
        "turmeric_dried",
        "curry_powder",
    ]

    # Inventory A — well stocked for green pasta
    inventory_a = [
        "spaghetti",
        "spinach",
        "peas",
        "cashews",
        "garlic",
        "lemon",
        "nutritional_yeast",
        "pine_nuts",
    ]

    # Inventory B — sparse, see what breaks
    inventory_b = [
        "farro",
        "kale",
        "white_beans",
        "lemon",
    ]

    # Inventory C — can it find the creamy tomato lentil?
    inventory_c = [
        "spaghetti",
        "tomato",
        "lentils",
        "cashews",
        "onion",
        "garlic",
        "miso",
        "lemon",
        "pumpkin_seeds",
    ]

    for label, inv in [("A", inventory_a), ("B", inventory_b), ("C", inventory_c)]:
        print(f"\n{'=' * 60}")
        print(f"Inventory {label}: {inv}")
        print("=" * 60)
        ranked = rank_recipes(RECIPES, inv + BASE_INVENTORY)
        for rs in ranked:
            print(f"\n  [{rs.score:.0%}] {rs.resolution.summary()}")
