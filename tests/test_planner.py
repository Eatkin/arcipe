import random
from collections import Counter

import pytest

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.planner import Lot
from garde.planner import consume
from garde.planner import deplete
from garde.planner import expire
from garde.planner import parse_inventory
from garde.planner import plan_week
from garde.planner import urgency
from garde.resolver import resolve


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


def test_several_lots_of_one_ingredient():
    from garde.loader import INGREDIENTS

    lots = parse_inventory({"tofu": [1, {"amount": 1, "expires": 2}]})
    assert len(lots) == 2
    assert {lot.expires_day for lot in lots} == {INGREDIENTS["tofu"].shelf_life_days, 2}


def test_opening_a_tin_gives_the_rest_a_short_date():
    ing = INGREDIENTS["coconut_milk"]
    lots = [Lot("coconut_milk", 3 * ing.unit_weight, None)]
    left = consume(lots, Counter({"coconut_milk": 100.0}), INGREDIENTS, day=2)
    assert sorted((lot.grams, lot.expires_day, lot.sealed) for lot in left) == [
        (ing.unit_weight - 100, 2 + ing.opened_days, False),
        (2 * ing.unit_weight, None, True),
    ]
    # The opened one goes first next time, and isn't opened again
    again = consume(left, Counter({"coconut_milk": 50.0}), INGREDIENTS, day=3)
    opened = [lot for lot in again if not lot.sealed]
    assert [lot.expires_day for lot in opened] == [2 + ing.opened_days]
    # Without db and day, nothing changes (old callers)
    assert len(consume(lots, Counter({"coconut_milk": 100.0}))) == 1


def test_a_half_used_tin_can_go_to_waste():
    plan = plan_week(
        {"coconut_veg_curry": RECIPES["coconut_veg_curry"]},
        {"coconut_milk": 1, "cabbage": 1, "tofu": 1},
        days=7,
        randomise=False,
    )
    # Half the tin goes in the curry; the open half doesn't keep the week
    assert plan.wasted["coconut_milk"] > 0
