"""
garde.loader
=============
Loads the ontology from YAML files into validated Pydantic models.
Replaces the monolithic ontology.py.

Usage:
    from garde.loader import INGREDIENTS, SLOTS, RECIPES, PREPARED_INGREDIENTS
"""

from __future__ import annotations

from pathlib import Path

import yaml

from garde.models import ComponentSlot
from garde.models import Ingredient
from garde.models import PreparedIngredient
from garde.models import Recipe
from garde.models import RecipeEdge


DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_ingredients(data_dir: Path = DATA_DIR) -> dict[str, Ingredient]:
    ingredients: dict[str, Ingredient] = {}
    ing_dir = data_dir / "ingredients"
    for path in sorted(ing_dir.glob("*.yaml")):
        raw = _load_yaml(path)
        for ing_id, data in raw.items():
            data["id"] = ing_id
            ingredients[ing_id] = Ingredient(**data)
    return ingredients


def load_prepared(data_dir: Path = DATA_DIR) -> dict[str, PreparedIngredient]:
    prepared: dict[str, PreparedIngredient] = {}
    raw = _load_yaml(data_dir / "prepared.yaml")
    for prep_id, data in raw.items():
        data["id"] = prep_id
        prepared[prep_id] = PreparedIngredient(**data)
    return prepared


def load_slots(data_dir: Path = DATA_DIR) -> dict[str, ComponentSlot]:
    slots: dict[str, ComponentSlot] = {}
    raw = _load_yaml(data_dir / "slots.yaml")
    for slot_id, data in raw.items():
        data["id"] = slot_id
        slots[slot_id] = ComponentSlot(**data)
    return slots


def load_recipes(
    data_dir: Path = DATA_DIR,
    slots: dict[str, ComponentSlot] | None = None,
) -> dict[str, Recipe]:
    if slots is None:
        slots = load_slots(data_dir)
    recipes: dict[str, Recipe] = {}
    recipe_dir = data_dir / "recipes"
    for path in sorted(recipe_dir.glob("*.yaml")):
        raw = _load_yaml(path)
        for recipe_id, data in raw.items():
            data["id"] = recipe_id
            # Resolve slot ids to ComponentSlot objects, applying any per-recipe
            # overrides, e.g. slot_overrides: {curry_protein: {max_ingredients: 1}}
            overrides: dict[str, dict] = data.pop("slot_overrides", None) or {}
            unknown = set(overrides) - set(data["slots"])
            if unknown:
                raise ValueError(
                    f"{recipe_id}: slot_overrides for slots not in recipe: {sorted(unknown)}"
                )
            data["slots"] = [
                ComponentSlot(**{**slots[s].model_dump(), **overrides.get(s, {})})
                for s in data["slots"]
            ]
            # Parse edges
            data["edges"] = [
                RecipeEdge(
                    from_slot=e["from"],
                    to_slot=e["to"],
                    edge_type=e["type"],
                )
                for e in data.get("edges", [])
            ]
            recipes[recipe_id] = Recipe(**data)
    return recipes


def _check_prepared(
    ingredients: dict[str, Ingredient], prepared: dict[str, PreparedIngredient]
) -> list[str]:
    problems: list[str] = []
    for prep in prepared.values():
        if prep.id in ingredients:
            problems.append(f"prepared {prep.id}: id clashes with an ingredient")
        problems += [
            f"prepared {prep.id}: unknown base ingredient {base!r}"
            for base in prep.base_ingredients
            if base not in ingredients
        ]
    return problems


def _check_recipe(recipe: Recipe, known: set[str]) -> list[str]:
    problems = [
        f"recipe {recipe.id}: {field_name} has unknown {ing_id!r}"
        for field_name in ("required_ingredients", "preferred_ingredients")
        for ing_id in getattr(recipe, field_name)
        if ing_id not in known
    ]
    slot_ids = {s.id for s in recipe.slots}
    problems += [
        f"recipe {recipe.id}: edge references missing slot {end!r}"
        for edge in recipe.edges
        for end in (edge.from_slot, edge.to_slot)
        if end not in slot_ids
    ]
    return problems


def check_references(
    ingredients: dict[str, Ingredient],
    prepared: dict[str, PreparedIngredient],
    recipes: dict[str, Recipe],
) -> list[str]:
    """Return a list of dangling id references in the data. Empty means consistent."""
    known = set(ingredients) | set(prepared)
    problems = _check_prepared(ingredients, prepared)
    for ing in ingredients.values():
        src = ing.made_from.ingredient if ing.made_from else None
        if src is not None and src not in ingredients:
            problems.append(f"ingredient {ing.id}: made_from unknown {src!r}")
        elif src is not None and ingredients[src].made_from is not None:
            problems.append(f"ingredient {ing.id}: made_from {src!r} is made itself")
    for recipe in recipes.values():
        problems += _check_recipe(recipe, known)
    return problems


# ---------------------------------------------------------------------------
# Module-level singletons — same interface as old ontology.py
# ---------------------------------------------------------------------------

INGREDIENTS = load_ingredients()
PREPARED_INGREDIENTS = load_prepared()
SLOTS = load_slots()
RECIPES = load_recipes(slots=SLOTS)


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Ingredients:          {len(INGREDIENTS)}")
    print(f"Prepared ingredients: {len(PREPARED_INGREDIENTS)}")
    print(f"Slots:                {len(SLOTS)}")
    print(f"Recipes:              {len(RECIPES)}")
    for problem in check_references(INGREDIENTS, PREPARED_INGREDIENTS, RECIPES):
        print(f"  ! {problem}")
    print()
    for r in RECIPES.values():
        print(f"  [{r.id}] {r.name} — {len(r.slots)} slots")
