import random
from collections import Counter

import pytest

from arcipe.loader import RECIPES
from arcipe.planner import Lot
from arcipe.planner import consume
from arcipe.planner import deplete
from arcipe.planner import expire
from arcipe.planner import parse_inventory
from arcipe.planner import plan_week
from arcipe.planner import urgency
from arcipe.resolver import resolve


def test_parse_inventory_units_grams_and_expiry():
    lots = parse_inventory(
        {
            "carrot": 3,  # 3 x 80g
            "red_lentils": "0.5kg",
            "spinach": {"amount": "150g", "expires": 1},
        }
    )
    by_id = {lot.ingredient_id: lot for lot in lots}
    assert by_id["carrot"].grams == 240
    assert by_id["carrot"].expires_day == 21  # shelf life default
    assert by_id["red_lentils"].grams == 500
    assert by_id["red_lentils"].expires_day is None
    assert by_id["spinach"] == Lot("spinach", 150, 1)


def test_count_without_unit_weight_is_an_error():
    with pytest.raises(ValueError, match="unit_weight"):
        parse_inventory({"red_lentils": 2})


def test_expire_counts_waste():
    kept, wasted = expire([Lot("spinach", 200, 2), Lot("carrot", 80, 5)], day=3)
    assert kept == [Lot("carrot", 80, 5)]
    assert wasted == Counter({"spinach": 200})


def test_consume_takes_soonest_expiring_first():
    lots = [Lot("spinach", 200, 5), Lot("spinach", 200, 2), Lot("spinach", 200, None)]
    left = consume(lots, Counter({"spinach": 250}))
    assert sorted(left, key=lambda lot: lot.grams) == [
        Lot("spinach", 150, 5),
        Lot("spinach", 200, None),
    ]


def test_urgency_rises_as_expiry_nears():
    lots = [Lot("spinach", 100, 1), Lot("carrot", 100, 5), Lot("potato", 100, 30)]
    u = urgency(lots, day=1)
    assert u["spinach"] == 1.0
    assert 0 < u["carrot"] < 1
    assert u["potato"] == 0


def test_deplete_counts_prepared_forms_against_their_bases():
    r = resolve(RECIPES["katsu_curry"], ["apple", "tofu", "butter"], servings=2)
    assert "roux" in r.used
    # roux 30g/serving x 2, half of it butter
    after = deplete({"butter": 250.0}, r)
    assert after["butter"] == pytest.approx(220)


def test_planner_respects_meal_type():
    plan = plan_week(
        inventory={"banana": 3, "butter": "250g", "frozen_fruit": "500g"},
        days=3,
        meals_per_day=["breakfast"],
        rng=random.Random(0),
    )
    for slot in plan.slots:
        if slot.recipe_id:
            assert "breakfast" in RECIPES[slot.recipe_id].meal_type


@pytest.mark.parametrize("seed", range(10))
def test_planner_never_uses_more_than_in_stock(seed):
    inventory = {
        "spinach": 1,
        "carrot": 4,
        "cauliflower": 1,
        "red_lentils": "300g",
        "coconut_milk": 1,
    }
    plan = plan_week(inventory=inventory, days=5, rng=random.Random(seed))
    for ing_id, start in plan.inventory_start.items():
        left = plan.inventory_remaining.get(ing_id, 0.0)
        used = start - left - plan.wasted.get(ing_id, 0.0)
        assert left >= -1e-6
        assert used >= -1e-6


@pytest.mark.parametrize("seed", range(10))
def test_planner_rescues_greens_about_to_expire(seed):
    plan = plan_week(
        inventory={"spinach": {"amount": "200g", "expires": 1}, "potato": 2},
        days=3,
        rng=random.Random(seed),
    )
    assert plan.wasted["spinach"] == 0
    first = plan.slots[0].resolution
    assert first is not None
    assert "spinach" in first.consumed


def test_uneaten_food_past_its_date_is_waste():
    # nothing can use nettle on its own in 2 days with no other stock... but
    # the plan must at least account for it either way
    plan = plan_week(inventory={"nettle": {"amount": "500g", "expires": 1}}, days=2)
    eaten = sum(
        s.resolution.consumed.get("nettle", 0) for s in plan.slots if s.resolution
    )
    assert eaten + plan.wasted["nettle"] == pytest.approx(500)


@pytest.mark.parametrize("seed", range(5))
def test_time_limit_is_respected(seed):
    plan = plan_week(
        inventory={"spinach": 2, "potato": 4, "carrot": 6},
        days=5,
        max_minutes=30,
        rng=random.Random(seed),
    )
    for slot in plan.slots:
        if slot.resolution:
            assert slot.resolution.timing.total <= 30
