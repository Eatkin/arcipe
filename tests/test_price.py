import math
import random

import pytest

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.models import Price
from garde.models import parse_price
from garde.resolver import resolve
from garde.solver import optimise_week


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, Price.NEGLIGIBLE),
        ("4", Price.FANCY),
        ("cheap", Price.CHEAP),
        ("Pricey", Price.PRICEY),
        ("  basically free ", Price.NEGLIGIBLE),
        ("luxury", Price.FANCY),
        ("normal", Price.MODERATE),
    ],
)
def test_price_accepts_numbers_and_words(value, expected):
    assert parse_price(value) == expected


@pytest.mark.parametrize("bad", [5, -1, "dear", True, None])
def test_price_rejects_nonsense(bad):
    with pytest.raises((ValueError, TypeError)):
        parse_price(bad)


def test_price_anchors():
    expect = {
        "cumin": 0,
        "carrot": 1,
        "cabbage": 1,
        "cauliflower": 2,
        "broccoli": 2,
        "tofu": 3,
    }
    assert {i: int(INGREDIENTS[i].price) for i in expect} == expect
    assert all(
        ing.price == Price.NEGLIGIBLE
        for ing in INGREDIENTS.values()
        if "dried_spice" in ing.classes
    )


def test_in_stock_beats_buying():
    r = resolve(
        RECIPES["saag"],
        {"frozen_spinach": 900.0, "potato": 1000.0},
        servings=2,
        buyable=set(INGREDIENTS) - {"frozen_spinach", "potato"},
    )
    greens = next(a for a in r.assignments if a.slot_id == "saag_green")
    assert greens.assigned == ["frozen_spinach"]


def test_buyable_makes_the_impossible_possible():
    assert not resolve(RECIPES["stroganoff"], {}, servings=2).success
    r = resolve(RECIPES["stroganoff"], {}, servings=2, buyable=set(INGREDIENTS))
    assert r.success
    assert "mushrooms" in r.consumed


def test_optional_slots_dont_send_you_shopping():
    r = resolve(
        RECIPES["rajma"], {}, servings=2, buyable=set(INGREDIENTS), rng=random.Random(0)
    )
    herb = next(a for a in r.assignments if a.slot_id == "finish_herb")
    assert herb.assigned == []


THIN_FRIDGE = {"carrot": 4, "potato": 4, "red_lentils": "300g"}
# Dishes the unlimited pantry can't make on its own: they force a shop
NEEDS_SHOPPING = {
    r: RECIPES[r] for r in ("stroganoff", "saag", "noodle_stir_fry", "sauce_over_grain")
}


def _week(seed=0, shopping=True, **kwargs):
    kwargs.setdefault("inventory", THIN_FRIDGE)
    kwargs.setdefault("days", 5)
    kwargs.setdefault("recipes", NEEDS_SHOPPING)
    kwargs.setdefault("iterations", 250)
    return optimise_week(
        restarts=1, rng=random.Random(seed), shopping=shopping, **kwargs
    )


def test_shopping_buys_whole_packs_and_reports_spend():
    res = _week()
    assert res.plan.bought, "expected something on the list"
    for ing_id, packs in res.plan.bought.items():
        assert isinstance(packs, int) and packs >= 1
        assert not INGREDIENTS[ing_id].unlimited
    spend = sum(int(INGREDIENTS[i].price) * n for i, n in res.plan.bought.items())
    assert res.breakdown.price_points == spend
    assert "Shopping list" in res.plan.shopping_list()


def test_shopping_rescues_meals_that_couldnt_be_made():
    without = _week(shopping=False, iterations=100)
    assert without.breakdown.unplanned > _week().breakdown.unplanned


def test_perishables_arent_bought_for_after_they_go_off():
    res = _week(1, days=7)
    for slot in res.plan.slots:
        if not slot.resolution:
            continue
        for raw in slot.resolution.consumed:
            life = INGREDIENTS[raw].shelf_life_days
            if raw in res.plan.bought and raw not in THIN_FRIDGE and life is not None:
                assert slot.day <= life, (raw, slot.day, life)


def test_pricier_shopping_means_spending_less():
    from garde.solver import Weights

    normal = _week(2)
    stingy = _week(2, weights=Weights(price=5.0))
    assert stingy.breakdown.price_points <= normal.breakdown.price_points


def test_pack_grams_defaults():
    assert INGREDIENTS["bread"].pack_grams == 800
    assert INGREDIENTS["cauliflower"].pack_grams == 600  # unit weight
    assert INGREDIENTS["red_lentils"].pack_grams == 500
    assert math.isclose(INGREDIENTS["courgette"].pack_grams, 200)
