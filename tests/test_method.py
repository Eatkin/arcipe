import random

import pytest

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.method import display_name
from garde.method import steps
from garde.resolver import resolve


PLENTY = dict.fromkeys(INGREDIENTS, 10000.0)


@pytest.mark.parametrize("recipe_id", sorted(RECIPES))
def test_outline_names_every_picked_ingredient(recipe_id):
    recipe = RECIPES[recipe_id]
    res = resolve(recipe, PLENTY, INGREDIENTS, servings=2, rng=random.Random(0))
    assert res.success
    outline = steps(recipe, res)
    assert outline[-1] == "Serve."
    text = " ".join(outline).lower()
    for a in res.assignments:
        for ing_id in a.assigned:
            assert display_name(ing_id).lower() in text


def test_slots_come_after_what_cooks_into_them():
    recipe = RECIPES["butter_chickpeas"]
    res = resolve(recipe, PLENTY, INGREDIENTS, servings=2, rng=random.Random(0))
    outline = steps(recipe, res)
    first = {s.split(":")[0]: n for n, s in enumerate(outline)}
    assert (
        first["Curry Aromatic Base"] < first["Spice Bloom"] < first["Tomato Sauce Body"]
    )
