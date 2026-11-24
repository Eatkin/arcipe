"""
arcipe.loader
=============
Loads the ontology from YAML files into validated Pydantic models.
Replaces the monolithic ontology.py.

Usage:
    from arcipe.loader import INGREDIENTS, SLOTS, RECIPES, PREPARED_INGREDIENTS
"""

from __future__ import annotations

from pathlib import Path

import yaml

from arcipe.models import ComponentSlot
from arcipe.models import Ingredient
from arcipe.models import PreparedIngredient
from arcipe.models import Recipe
from arcipe.models import RecipeEdge


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
            # Resolve slot ids to ComponentSlot objects
            data["slots"] = [slots[s] for s in data["slots"]]
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
    print()
    for r in RECIPES.values():
        print(f"  [{r.id}] {r.name} — {len(r.slots)} slots")
