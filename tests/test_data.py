from pathlib import Path

import pytest
import yaml

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS
from garde.loader import RECIPES
from garde.loader import SLOTS
from garde.loader import check_references
from garde.loader import load_recipes


def test_data_has_no_dangling_references():
    assert check_references(INGREDIENTS, PREPARED_INGREDIENTS, RECIPES) == []


def test_recipe_slot_override_applies_only_to_that_recipe(tmp_path: Path):
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "r.yaml").write_text(
        yaml.safe_dump(
            {
                "lean_rajma": {
                    "name": "Lean Rajma",
                    "description": "x",
                    "slots": ["curry_grain_base", "curry_protein"],
                    "slot_overrides": {"curry_protein": {"max_ingredients": 1}},
                }
            }
        )
    )
    recipes = load_recipes(tmp_path, slots=SLOTS)
    protein = next(s for s in recipes["lean_rajma"].slots if s.id == "curry_protein")
    assert protein.max_ingredients == 1
    assert SLOTS["curry_protein"].max_ingredients == 2


def test_override_for_slot_not_in_recipe_is_an_error(tmp_path: Path):
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "r.yaml").write_text(
        yaml.safe_dump(
            {
                "bad": {
                    "name": "Bad",
                    "description": "x",
                    "slots": ["curry_grain_base"],
                    "slot_overrides": {"curry_protein": {"max_ingredients": 1}},
                }
            }
        )
    )
    with pytest.raises(ValueError, match="curry_protein"):
        load_recipes(tmp_path, slots=SLOTS)


def test_every_slot_says_how_much_it_needs():
    assert [s for s, slot in SLOTS.items() if slot.amount is None] == []


def test_base_ratios_only_name_real_bases():
    for prep in PREPARED_INGREDIENTS.values():
        assert set(prep.base_ratio) <= set(prep.base_ingredients), prep.id


def test_recipe_graphs_are_acyclic():
    from garde.timing import has_cycle

    assert [r for r in RECIPES.values() if has_cycle(r)] == []


def test_sources_are_unique_https_links():
    for recipe in RECIPES.values():
        assert len(recipe.sources) == len(set(recipe.sources)), recipe.id
        for url in recipe.sources:
            assert url.startswith("https://") and " " not in url, (recipe.id, url)
