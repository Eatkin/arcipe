"""Made ingredients (dried -> cooked), scraps, batch dishes and leftovers."""

import random
from collections import Counter

import pytest

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS
from garde.loader import RECIPES
from garde.planner import Lot
from garde.planner import cook_batches
from garde.planner import eat_leftovers
from garde.planner import expire_dishes
from garde.planner import leftover_key
from garde.planner import parse_inventory
from garde.planner import plan_week
from garde.planner import stash
from garde.resolver import Resolution
from garde.resolver import available_pool
from garde.resolver import makeable_grams
from garde.resolver import normalise_stock
from garde.resolver import resolve
from garde.solver import LEFTOVERS
from garde.solver import Weights
from garde.solver import _Problem
from garde.solver import optimise_week
from garde.solver import simulate


# ---------------------------------------------------------------- made from


def test_made_from_sources_exist_and_are_raw():
    for ing in INGREDIENTS.values():
        if ing.made_from:
            src = INGREDIENTS[ing.made_from.ingredient]
            assert src.made_from is None
            assert 0 < ing.made_from.ratio <= 1.5


def test_coconut_milk_can_be_made_from_cream():
    stock = normalise_stock({"coconut_cream": 400}, INGREDIENTS)
    assert "coconut_milk" in available_pool(stock, INGREDIENTS, PREPARED_INGREDIENTS)
    assert makeable_grams(
        "coconut_milk", stock, INGREDIENTS, PREPARED_INGREDIENTS
    ) == pytest.approx(800)


def test_chilli_from_dried_beans_cooks_them():
    inv = {
        "dried_kidney_beans": 500,
        "dried_black_beans": 500,
        "onion": 400,
        "carrot": 400,
        "bell_pepper": 300,
        "canned_tomato": 800,
    }
    res = resolve(RECIPES["bean_chilli"], inv, rng=random.Random(0), servings=2)
    assert res.success, res.summary()
    assert res.made, "beans should have been cooked from dried"
    for raw in res.made:
        assert raw in res.timing.make_ahead
        source = INGREDIENTS[raw].made_from.ingredient
        assert res.consumed[source] == pytest.approx(
            res.made[raw] * INGREDIENTS[raw].made_from.ratio
        )
    assert "kidney_beans" not in res.consumed  # none in stock, so all made


def test_stocked_beans_come_before_cooking_dried():
    inv = {
        "kidney_beans": 2000,
        "dried_kidney_beans": 500,
        "black_beans": 2000,
        "onion": 400,
        "carrot": 400,
        "bell_pepper": 300,
        "canned_tomato": 800,
    }
    res = resolve(RECIPES["bean_chilli"], inv, rng=random.Random(0), servings=2)
    assert res.success
    assert not res.made


def test_cooking_beans_makes_a_whole_pot():
    res = Resolution("x", "x", [], set(), [], made=Counter({"chickpeas": 240}))
    lots = [Lot("dried_chickpeas", 500.0)]
    lots, made = cook_batches(lots, res, day=2, db=INGREDIENTS)
    batch = INGREDIENTS["chickpeas"].made_from
    cooked = [lot for lot in lots if lot.ingredient_id == "chickpeas"]
    assert len(cooked) == 1
    assert cooked[0].grams == pytest.approx(batch.batch - 240)
    assert cooked[0].expires_day == 2 + batch.keeps_days
    dried = sum(lot.grams for lot in lots if lot.ingredient_id == "dried_chickpeas")
    assert dried == pytest.approx(500 - (batch.batch - 240) * batch.ratio)
    assert made["chickpeas"] == pytest.approx(batch.batch)


def test_a_pot_is_limited_by_the_dried_beans_left():
    res = Resolution("x", "x", [], set(), [], made=Counter({"chickpeas": 240}))
    lots, _ = cook_batches([Lot("dried_chickpeas", 42.0)], res, 1, INGREDIENTS)
    cooked = sum(lot.grams for lot in lots if lot.ingredient_id == "chickpeas")
    assert cooked == pytest.approx(100)
    assert not [lot for lot in lots if lot.ingredient_id == "dried_chickpeas"]


# ---------------------------------------------------------------- scraps


FRIDGE = {
    "apple": 3,
    "banana": 3,
    "carrot": 5,
    "onion": 3,
    "potato": 5,
    "spinach": 1,
    "tomato": 4,
    "cauliflower": 1,
    "mushrooms": 1,
    "dried_chickpeas": "500g",
    "red_lentils": "500g",
}


@pytest.mark.parametrize("recipe_id", sorted(RECIPES))
def test_no_perishable_scraps_left(recipe_id):
    """Cooking never leaves a perishable with less than a crumb of it."""
    stock = dict(
        normalise_stock(
            {lot.ingredient_id: lot.grams for lot in parse_inventory(FRIDGE)},
            INGREDIENTS,
        ).items()
    )
    res = resolve(RECIPES[recipe_id], stock, rng=random.Random(0), servings=2)
    for raw, grams in res.consumed.items():
        ing = INGREDIENTS[raw]
        left = stock.get(raw, 0.0) - grams
        if ing.perishable and stock.get(raw, 0.0) != float("inf"):
            assert left <= 1e-6 or left >= ing.crumb - 1e-6, (raw, left)


def test_crumb_defaults():
    assert INGREDIENTS["apple"].crumb == min(INGREDIENTS["apple"].unit_weight / 2, 50)
    assert INGREDIENTS["red_lentils"].crumb == 15


# ---------------------------------------------------------------- leftovers


def _cooked(recipe_id, servings, consumed=1000.0):
    return Resolution(
        recipe_id,
        RECIPES[recipe_id].name,
        [],
        set(),
        [],
        consumed=Counter({"x": consumed}),
        servings=servings,
    )


def test_a_loaf_leaves_portions_for_later():
    bread = RECIPES["vegan_banana_bread"]
    dishes, wasted = stash([], bread, _cooked(bread.id, 10), people=2, day=1)
    assert not wasted
    (d,) = dishes
    assert d.portions == 8
    assert d.grams_per_portion == pytest.approx(100)
    assert d.expires_day == 1 + bread.keeps_days


def test_leftovers_of_a_dish_that_does_not_keep_are_waste():
    recipe = RECIPES["noodle_soup"]
    assert recipe.keeps_days == 0
    dishes, wasted = stash([], recipe, _cooked(recipe.id, 4), people=2, day=1)
    assert not dishes
    assert wasted[leftover_key(recipe.id)] == pytest.approx(500)


def test_eat_leftovers_takes_the_soonest_and_respects_meal_type():
    bread = RECIPES["vegan_banana_bread"]
    chilli = RECIPES["bean_chilli"]
    dishes, _ = stash([], bread, _cooked(bread.id, 10), 2, day=1)
    dishes, _ = stash(dishes, chilli, _cooked(chilli.id, 4), 2, day=2)
    _, dish, _ = eat_leftovers(dishes, "dinner", 2)
    assert dish is not None and dish.recipe_id == "bean_chilli"
    dishes, dish, _ = eat_leftovers(dishes, "breakfast", 2)
    assert dish is not None and dish.recipe_id == "vegan_banana_bread"
    assert next(d for d in dishes if d.recipe_id == bread.id).portions == 6
    dishes, dish, _ = eat_leftovers(dishes, "dinner", 3)  # only 2 portions of chilli
    assert dish is None


def test_two_dishes_can_make_one_leftovers_meal():
    chilli = RECIPES["bean_chilli"]
    rajma = RECIPES["rajma"]
    dishes, _ = stash([], chilli, _cooked(chilli.id, 3), 2, day=1)  # 1 spare
    dishes, _ = stash(dishes, rajma, _cooked(rajma.id, 4), 2, day=2)  # 2 spare
    left, dish, mix = eat_leftovers(dishes, "dinner", 3)
    assert dish is not None
    assert mix == {"bean_chilli": 1, "rajma": 2}
    assert left == []
    # One dish that stretches to everyone is still preferred to a mix
    _, dish, mix = eat_leftovers(dishes, "dinner", 2)
    assert dish.recipe_id == "bean_chilli" or mix == {"rajma": 2}
    assert len(mix) == 1


def test_old_leftovers_go_off():
    chilli = RECIPES["bean_chilli"]
    dishes, _ = stash([], chilli, _cooked(chilli.id, 4), 2, day=1)
    kept, wasted = expire_dishes(dishes, 1 + chilli.keeps_days)
    assert kept and not wasted
    kept, wasted = expire_dishes(dishes, 2 + chilli.keeps_days)
    assert not kept
    assert wasted[leftover_key(chilli.id)] == pytest.approx(500)  # 2 of 4 portions


BREAD_CUPBOARD = {"banana": {"amount": 4, "expires": 3}, "coconut_oil": "300g"}


def _problem(meals, days, inventory):
    return _Problem(
        lots=parse_inventory(inventory),
        days=days,
        meals=meals,
        recipes=RECIPES,
        ingredient_db=INGREDIENTS,
        people=2,
        max_minutes=None,
        gap=2,
        weights=Weights(),
    )


def test_one_loaf_feeds_several_breakfasts():
    p = _problem(["breakfast"], 5, BREAD_CUPBOARD)
    plan, b = simulate(("vegan_banana_bread", *[LEFTOVERS] * 4), p)
    assert all(s.filled for s in plan.slots), plan.summary(use_color=False)
    assert [s.leftovers_from for s in plan.slots] == [None, 1, 1, 1, 1]
    assert b.repeats == 0
    assert b.leftover_meals == pytest.approx(4 * 0.25)
    assert not plan.leftovers  # 10 portions, 5 breakfasts for 2


def test_leftovers_run_out():
    p = _problem(["breakfast"], 6, BREAD_CUPBOARD)
    plan, _ = simulate(("vegan_banana_bread", *[LEFTOVERS] * 5), p)
    assert plan.unplanned == [(6, "breakfast")]


def test_cooking_double_feeds_tomorrow():
    inv = {
        "dried_kidney_beans": "500g",
        "dried_black_beans": "500g",
        "onion": 6,
        "carrot": 6,
        "bell_pepper": 3,
        "canned_tomato": 4,
    }
    p = _problem(["dinner"], 2, inv)
    plan, _ = simulate(("bean_chilli*2", LEFTOVERS), p)
    assert all(s.filled for s in plan.slots), plan.summary(use_color=False)
    assert plan.slots[0].resolution.servings == 4
    assert plan.slots[1].leftovers_from == 1
    single, _ = simulate(("bean_chilli", LEFTOVERS), p)
    assert single.unplanned == [(2, "dinner")]


def test_greedy_planner_eats_leftovers_first():
    plan = plan_week(
        inventory={**BREAD_CUPBOARD, "oats": "1kg"},
        days=3,
        meals_per_day=["breakfast"],
        randomise=False,
    )
    cooked = [s for s in plan.slots if s.recipe_id == "vegan_banana_bread"]
    if cooked and cooked[0].leftovers_from is None:
        later = [s for s in plan.slots if s.day > cooked[0].day]
        assert all(s.leftovers_from == cooked[0].day for s in later)


def test_solver_bakes_bread_once_not_daily():
    result = optimise_week(
        inventory={"banana": {"amount": 3, "expires": 4}},
        days=4,
        meals_per_day=["breakfast"],
        iterations=400,
        restarts=1,
        rng=random.Random(0),
    )
    bakes = [
        s
        for s in result.plan.slots
        if s.recipe_id == "vegan_banana_bread" and s.leftovers_from is None
    ]
    assert len(bakes) == 1
    assert sum(s.leftovers_from is not None for s in result.plan.slots) >= 2
