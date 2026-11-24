import random

from arcipe.loader import RECIPES
from arcipe.planner import deplete
from arcipe.planner import plan_week
from arcipe.resolver import resolve


def test_deplete_counts_prepared_forms_against_their_bases():
    r = resolve(RECIPES["katsu_curry"], ["apple", "tofu", "butter"])
    assert "roux" in r.used
    after = deplete({"butter": 2, "tofu": 1, "apple": 1}, r)
    assert after["butter"] == 1


def test_deplete_leaves_unlimited_alone():
    r = resolve(RECIPES["katsu_curry"], ["apple", "tofu", "butter"])
    after = deplete({"butter": 2, "plain_flour": 100}, r)
    assert after["plain_flour"] == 100


def test_planner_respects_meal_type():
    plan = plan_week(
        inventory={"banana": 3, "butter": 2, "frozen_fruit": 2},
        days=3,
        meals_per_day=["breakfast"],
        rng=random.Random(0),
    )
    for slot in plan.slots:
        if slot.recipe_id:
            assert "breakfast" in RECIPES[slot.recipe_id].meal_type


def test_planner_never_uses_more_than_in_stock():
    start = {
        "spinach": 1,
        "carrot": 2,
        "cauliflower": 1,
        "red_lentils": 2,
        "coconut_milk": 1,
    }
    plan = plan_week(inventory=start, days=5, rng=random.Random(1))
    for ing_id, portions in plan.inventory_remaining.items():
        assert 0 <= portions <= start[ing_id]
