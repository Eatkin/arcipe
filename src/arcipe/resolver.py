"""
Recipe Graph resolution
=======================
Resolves abstract recipes into concrete ingredient assignments given an inventory.

Flow:
    available_pool(inventory)   -> raw ids + unlimited staples + makeable prepared forms
    candidates(slot, pool)      -> ingredients matching the slot's roles/method/colour
    score(ingredient, slot)     -> float
    resolve(recipe, inventory)  -> Resolution

Reuse rule (recipe.no_ingredient_reuse): every pick "claims" the raw ingredients it is
made from (its prepared-form bases, or itself). A later pick that would claim one of the
same raw ingredients is rejected, so carrot and roasted_carrot can't both appear.
Ingredients flagged `staple` are never claimed and can be used freely.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from itertools import groupby

from arcipe.loader import INGREDIENTS
from arcipe.loader import PREPARED_INGREDIENTS
from arcipe.models import COLOUR_MAX
from arcipe.models import CUISINE_FAMILY
from arcipe.models import ComponentSlot
from arcipe.models import Ingredient
from arcipe.models import PreparedIngredient
from arcipe.models import Recipe
from arcipe.models import Season
from arcipe.models import colour_distance
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
    used: set[str]  # ingredient ids (raw or prepared) assigned across all slots
    unfillable: list[str]  # slot ids that could not be filled (required only)
    missing_required: list[str] = field(default_factory=list)  # not available at all
    unplaceable_required: list[str] = field(default_factory=list)  # no slot accepts it
    # Raw ingredient ids actually consumed, with prepared forms expanded to their bases
    consumed: Counter[str] = field(default_factory=Counter)

    @property
    def success(self) -> bool:
        return not (
            self.unfillable or self.missing_required or self.unplaceable_required
        )

    def summary(self) -> str:
        lines = [f"[{self.recipe_name}] {'✓' if self.success else '✗'}"]
        for a in self.assignments:
            status = "✓" if a.filled else "✗ UNFILLABLE"
            assigned = ", ".join(a.assigned) if a.assigned else "—"
            lines.append(f"  {a.slot_label:<22} {status}  →  {assigned}")
        if self.missing_required:
            lines.append(f"\n  Missing required: {', '.join(self.missing_required)}")
        if self.unplaceable_required:
            lines.append(
                f"\n  Required but no slot accepts: {', '.join(self.unplaceable_required)}"
            )
        if self.unfillable:
            lines.append(f"\n  Unfillable slots: {', '.join(self.unfillable)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inventory expansion
# ---------------------------------------------------------------------------


def available_pool(
    inventory: list[str],
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> set[str]:
    """
    Everything that could go into a recipe: raw ingredients on hand, unlimited
    staples, and any prepared form whose base ingredients are all available.
    """
    raw = {i for i in inventory if i in ingredient_db}
    raw |= {i for i, ing in ingredient_db.items() if ing.unlimited}
    makeable = {
        p.id for p in prepared_db.values() if all(b in raw for b in p.base_ingredients)
    }
    return raw | makeable


def expand_inventory(
    inventory: list[str],
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> dict[str, Ingredient]:
    """
    Return ingredient_db plus a synthetic Ingredient for every prepared form whose
    bases are all available, so prepared forms can be matched and scored like raw ones.
    """
    expanded = dict(ingredient_db)
    pool = available_pool(inventory, ingredient_db, prepared_db)
    for prep in prepared_db.values():
        if prep.id not in pool:
            continue
        bases = [ingredient_db[b] for b in prep.base_ingredients]
        base = bases[0]
        expanded[prep.id] = Ingredient(
            id=prep.id,
            name=f"{prep.id.replace('_', ' ')} (prepared)",
            classes=base.classes,
            roles=prep.unlocked_roles,
            flavour=prep.flavour or base.flavour,
            colour=prep.colour or base.colour,
            season=base.season,
            cost=base.cost,
            nutrition=base.nutrition,
            perishable=any(b.perishable for b in bases),
            unlimited=all(b.unlimited for b in bases),
            cuisine_context=prep.cuisine_context,
            notes=prep.notes,
        )
    return expanded


def consumes(ing_id: str, prepared_db: dict[str, PreparedIngredient]) -> list[str]:
    """Raw ingredient ids used up by assigning `ing_id` (its bases if prepared)."""
    prep = prepared_db.get(ing_id)
    return list(prep.base_ingredients) if prep else [ing_id]


def reuse_keys(
    ing_id: str,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> set[str]:
    """Raw ingredients an assignment claims under no_ingredient_reuse (staples exempt)."""
    return {
        raw
        for raw in consumes(ing_id, prepared_db)
        if not (raw in ingredient_db and ingredient_db[raw].staple)
    }


# ---------------------------------------------------------------------------
# Candidate matching
# ---------------------------------------------------------------------------


def matches_slot(
    ing: Ingredient,
    slot: ComponentSlot,
    prepared_db: dict[str, PreparedIngredient],
    min_coverage: float = 1.0,
) -> bool:
    """Does this ingredient satisfy the slot's colour, prep method and role requirements?"""
    if slot.required_colour:
        dist = colour_distance(ing.colour, slot.required_colour)
        if dist / COLOUR_MAX > slot.colour_tolerance:
            return False
    # A prepared form has a fixed method (roasted carrot is roasted); raw ingredients
    # can be cooked however the slot needs.
    prep = prepared_db.get(ing.id)
    if prep and slot.required_method and prep.method not in slot.required_method:
        return False
    required = set(slot.required_roles)
    covered = required & set(ing.roles)
    coverage = len(covered) / len(required) if required else 1.0
    return coverage >= min_coverage


def candidates(
    slot: ComponentSlot,
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
    min_coverage: float = 1.0,
    prepared_db: dict[str, PreparedIngredient] | None = None,
) -> list[Ingredient]:
    """
    Return all ingredients from inventory that satisfy the slot.
    min_coverage=1.0 enforces full role coverage (default).
    Lower values allow partial matches — useful for 'almost there' suggestions.
    """
    db = ingredient_db or INGREDIENTS
    preps = PREPARED_INGREDIENTS if prepared_db is None else prepared_db
    result = []
    for ing_id in inventory:
        ing = db.get(ing_id)
        if ing is not None and matches_slot(ing, slot, preps, min_coverage):
            result.append(ing)
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

CURRENT_SEASON: Season = get_season(date.today())

COST_SCORE: dict[str, float] = {
    "very_low": 1.0,
    "low": 0.8,
    "medium": 0.5,
    "high": 0.2,
}


def score(ingredient: Ingredient, slot: ComponentSlot, recipe: Recipe) -> float:
    """
    Score an ingredient against a slot. Higher is better.

    Components:
        role_coverage     — fraction of slot's required_roles covered
                            (always 1.0 after candidate filtering, but useful if we relax later)
        preferred_class   — bonus if ingredient is in slot.preferred_classes
        preferred_role    — bonus if ingredient also covers a slot.preferred_roles role
        season            — bonus if ingredient is in season or available all year
        cost              — cheaper ingredients score higher (maximise use of cheap staples)
        role_depth        — ingredients with more roles are more versatile, slight bonus
        perishable        — use things that go off before things that don't
        preferred         — recipe.preferred_ingredients bonus
        cuisine_context   — bonus for exact cuisine match, small bonus for same family,
                            heavy penalty for a different family
    """
    s = 0.0

    required = set(slot.required_roles)
    covered = required & set(ingredient.roles)
    s += len(covered) / len(required) if required else 1.0

    if any(c in slot.preferred_classes for c in ingredient.classes):
        s += 0.5

    if any(r in slot.preferred_roles for r in ingredient.roles):
        s += 0.4

    if "all" in ingredient.season or CURRENT_SEASON in ingredient.season:
        s += 0.3
    elif "forage" in ingredient.season:
        s += 0.4  # forage ingredients are free, reward them

    s += COST_SCORE.get(ingredient.cost, 0.5)

    s += min(len(ingredient.roles) / 10, 0.2)

    if ingredient.perishable:
        s += 0.3

    if ingredient.id in recipe.preferred_ingredients:
        s += 0.5

    if recipe.cuisine_context and ingredient.cuisine_context:
        if set(recipe.cuisine_context) & set(ingredient.cuisine_context):
            s += 0.4
        else:
            recipe_families = {CUISINE_FAMILY[c] for c in recipe.cuisine_context}
            ing_families = {CUISINE_FAMILY[c] for c in ingredient.cuisine_context}
            s += 0.1 if recipe_families & ing_families else -0.8

    return s


def rank(
    viable: list[Ingredient],
    slot: ComponentSlot,
    recipe: Recipe,
    rng: random.Random | None = None,
) -> list[Ingredient]:
    """Sort by score descending, shuffling within equal-score groups for variety."""
    rng = rng or random.Random()
    scored = sorted(
        ((score(ing, slot, recipe), ing.id, ing) for ing in viable),
        key=lambda t: (-t[0], t[1]),
    )
    ranked: list[Ingredient] = []
    for _, group in groupby(scored, key=lambda t: t[0]):
        group_list = [ing for _, _, ing in group]
        rng.shuffle(group_list)
        ranked.extend(group_list)
    return ranked


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass
class _Assigner:
    """Tracks what has been put in each slot and which raw ingredients are claimed."""

    recipe: Recipe
    ingredient_db: dict[str, Ingredient]
    prepared_db: dict[str, PreparedIngredient]
    chosen: dict[str, list[str]] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.chosen = {slot.id: [] for slot in self.recipe.slots}

    def _keys(self, ing_id: str) -> set[str]:
        if not self.recipe.no_ingredient_reuse:
            return set()
        return reuse_keys(ing_id, self.ingredient_db, self.prepared_db)

    def can_take(self, slot: ComponentSlot, ing_id: str) -> bool:
        current = self.chosen[slot.id]
        if len(current) >= slot.max_ingredients or ing_id in current:
            return False
        return not self._keys(ing_id) & self.claimed

    def take(self, slot: ComponentSlot, ing_id: str) -> None:
        self.chosen[slot.id].append(ing_id)
        self.claimed |= self._keys(ing_id)


def resolve(
    recipe: Recipe,
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
    prepared_db: dict[str, PreparedIngredient] | None = None,
    rng: random.Random | None = None,
) -> Resolution:
    """
    Resolve a recipe against an inventory.

        1. Build the pool: inventory + unlimited staples + makeable prepared forms
        2. Every required ingredient must be in the pool, and is placed first into
           the slot that accepts it and scores it highest
        3. Each slot (in order) is topped up to max_ingredients with the best-scoring
           candidates that don't break the reuse rule
        4. Required slots left empty are recorded as unfillable
    """
    db = ingredient_db or INGREDIENTS
    preps = PREPARED_INGREDIENTS if prepared_db is None else prepared_db
    expanded_db = expand_inventory(inventory, db, preps)
    pool = sorted(available_pool(inventory, db, preps))
    failed = Resolution(recipe.id, recipe.name, [], set(), [])

    failed.missing_required = [i for i in recipe.required_ingredients if i not in pool]
    if failed.missing_required:
        return failed

    picks = _Assigner(recipe, db, preps)

    # Place required ingredients first so nothing else can take their slot
    for req in recipe.required_ingredients:
        ing = expanded_db[req]
        accepting = [
            slot
            for slot in recipe.slots
            if matches_slot(ing, slot, preps) and picks.can_take(slot, req)
        ]
        if not accepting:
            failed.unplaceable_required.append(req)
            continue
        picks.take(max(accepting, key=lambda sl, i=ing: score(i, sl, recipe)), req)
    if failed.unplaceable_required:
        return failed

    assignments: list[SlotAssignment] = []
    for slot in recipe.slots:
        viable = candidates(slot, pool, expanded_db, prepared_db=preps)
        ranked = rank(viable, slot, recipe, rng)
        for ing in ranked:
            if picks.can_take(slot, ing.id):
                picks.take(slot, ing.id)
        assignments.append(
            SlotAssignment(
                slot_id=slot.id,
                slot_label=slot.label,
                assigned=list(picks.chosen[slot.id]),
                candidates=[ing.id for ing in ranked],
                filled=bool(picks.chosen[slot.id]) or slot.optional,
            )
        )

    chosen_ids = [i for ids in picks.chosen.values() for i in ids]
    return Resolution(
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        assignments=assignments,
        used=set(chosen_ids),
        unfillable=[a.slot_id for a in assignments if not a.filled],
        consumed=Counter(raw for i in chosen_ids for raw in consumes(i, preps)),
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
    rng: random.Random | None = None,
) -> list[RecipeScore]:
    """
    Resolve all recipes against inventory and rank by how completely they can be filled.
    Useful for the meal planner: given what I have, what can I actually make?
    """
    results = []
    for recipe in recipes.values():
        r = resolve(recipe, inventory, ingredient_db, rng=rng)
        required_slots = [
            a for a in r.assignments if not _slot_is_optional(a.slot_id, recipe)
        ]
        filled = sum(1 for a in required_slots if a.filled)
        total = len(required_slots)
        if r.missing_required or r.unplaceable_required:
            recipe_score = 0.0
        else:
            recipe_score = filled / total if total else 1.0
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
    from arcipe.loader import RECIPES

    # We always got spices
    BASE_INVENTORY = [
        "garam_masala",
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
        "cannellini_beans",
        "lemon",
    ]

    # Inventory C — can it find the creamy tomato lentil?
    inventory_c = [
        "spaghetti",
        "tomato",
        "red_lentils",
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
