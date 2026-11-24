import random

import pytest

from arcipe.loader import RECIPES
from arcipe.solver import Weights
from arcipe.solver import optimise_week
from arcipe.solver import weights_from


FRIDGE = {
    "spinach": {"amount": 1, "expires": 2},
    "kale": {"amount": 1, "expires": 3},
    "mushrooms": {"amount": 2, "expires": 4},
    "carrot": 8,
    "cauliflower": 1,
    "tomato": 3,
    "coriander_leaf": 2,
    "potato": 4,
    "red_lentils": "500g",
    "coconut_milk": 2,
    "cannellini_beans": 2,
    "tofu": 1,
    "apple": 2,
    "butter": "250g",
}


def _solve(seed=0, **kwargs):
    kwargs.setdefault("inventory", FRIDGE)
    kwargs.setdefault("iterations", 400)
    kwargs.setdefault("restarts", 1)
    return optimise_week(rng=random.Random(seed), **kwargs)


@pytest.mark.parametrize("seed", range(3))
def test_never_worse_than_greedy(seed):
    result = _solve(seed)
    assert result.breakdown.objective <= result.greedy.objective + 1e-9


def test_beats_greedy_on_waste_when_looking_ahead_matters():
    result = _solve(0, iterations=1500)
    assert result.breakdown.waste_g < result.greedy.waste_g


def test_same_seed_same_plan():
    a = _solve(3)
    b = _solve(3)
    assert [s.recipe_id for s in a.plan.slots] == [s.recipe_id for s in b.plan.slots]


def test_time_limit_is_hard():
    result = _solve(1, max_minutes=30)
    for slot in result.plan.slots:
        if slot.resolution:
            assert slot.resolution.timing.total <= 30


def test_meal_types_respected():
    result = _solve(2, meals_per_day=["breakfast", "dinner"], days=3)
    for slot in result.plan.slots:
        if slot.recipe_id:
            assert slot.meal in RECIPES[slot.recipe_id].meal_type


def test_heavy_waste_weight_never_wastes_more():
    light = _solve(4, weights=Weights(waste=0.1, spill=0.1), iterations=800)
    heavy = _solve(4, weights=Weights(waste=10.0, spill=10.0), iterations=800)
    assert heavy.breakdown.waste_g + heavy.breakdown.spill_g <= (
        light.breakdown.waste_g + light.breakdown.spill_g
    )


def test_breakdown_matches_the_plan():
    result = _solve(5)
    assert result.breakdown.waste_g == pytest.approx(sum(result.plan.wasted.values()))
    assert result.breakdown.unplanned == len(result.plan.unplanned)


def test_unknown_weight_is_an_error():
    assert weights_from({"waste": 3}).waste == 3
    with pytest.raises(ValueError, match="vibes"):
        weights_from({"vibes": 1})
