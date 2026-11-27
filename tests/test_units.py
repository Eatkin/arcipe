from collections import Counter

import pytest

from garde import pantry
from garde.dashboard import _slot_item
from garde.dashboard import meta
from garde.loader import INGREDIENTS
from garde.pantry import Item
from garde.planner import MealPlan
from garde.planner import parse_amount
from garde.units import format_amount
from garde.units import stock_amount
from garde.units import unit


SOY = INGREDIENTS["soy_sauce"].model_copy(
    update={"id": "test_soy", "density": 1.2, "unlimited": False, "pack_weight": 180}
)
OIL = INGREDIENTS["soy_sauce"].model_copy(update={"id": "test_oil", "density": 0.9})
WATERY = INGREDIENTS["soy_sauce"].model_copy(
    update={"id": "test_milk", "density": 1.0, "unit_weight": 1000.0}
)
CARROT = INGREDIENTS["carrot"]
DB = {i.id: i for i in (SOY, OIL, WATERY, CARROT)}


@pytest.mark.parametrize(
    "ing, grams, shown",
    [
        (SOY, 18, "15ml"),
        (SOY, 3, "2.5ml"),
        (OIL, 450, "500ml"),
        (WATERY, 1000, "1l"),
        (WATERY, 1250, "1.25l"),
        (WATERY, 999.7, "1l"),
        (WATERY, 0, "0ml"),
        (CARROT, 240, "240g"),
        (CARROT, 2400, "2400g"),
        (None, 120.4, "120g"),
    ],
)
def test_format_amount(ing, grams, shown):
    assert format_amount(ing, grams) == shown


def test_unit():
    assert unit(SOY) == "ml"
    assert unit(CARROT) == "g"
    assert unit(None) == "g"


def test_stock_amount_reads_back_through_parse_amount():
    for ing, grams in [(SOY, 360), (OIL, 92.7), (WATERY, 2500), (CARROT, 240)]:
        text = stock_amount(ing, grams, 1)
        assert parse_amount(ing, text) == pytest.approx(grams, abs=0.2)
    assert stock_amount(SOY, 360) == "300ml"
    assert stock_amount(CARROT, 240) == "240g"


def test_millilitres_go_through_density():
    assert parse_amount(SOY, "100ml") == pytest.approx(120)
    assert parse_amount(SOY, "1.5l") == pytest.approx(1800)
    assert parse_amount(OIL, "500ml") == pytest.approx(450)
    assert parse_amount(SOY, "100g") == 100  # grams are grams
    assert parse_amount(CARROT, "100ml") == 100  # no density: 1g per ml


def test_density_must_be_positive():
    with pytest.raises(ValueError):
        SOY.model_validate({**SOY.model_dump(), "density": 0})


def test_pantry_bare_numbers_are_ml_for_liquids():
    assert pantry.grams(Item("test_soy", "100"), DB) == pytest.approx(120)
    assert pantry.grams(Item("carrot", "100g"), DB) == 100


def test_pantry_keeps_liquids_in_ml_after_taking_some():
    left, taken = pantry.take([Item("test_soy", "500ml")], {"test_soy": 120}, DB)
    assert [i.amount for i in left] == ["400ml"]
    assert [i.amount for i in taken] == ["100ml"]


def test_shopping_items_show_ml_for_liquids():
    plan = MealPlan(
        days=1,
        meals_per_day=["dinner"],
        slots=[],
        inventory_start={},
        inventory_remaining={},
        bought=Counter({"test_soy": 2, "carrot": 1}),
    )
    rows = {r["id"]: r for r in plan.shopping_items(DB)}
    assert rows["test_soy"]["grams"] == 360
    assert rows["test_soy"]["amount"] == "300ml"
    assert rows["test_soy"]["unit"] == "ml"
    assert rows["carrot"]["unit"] == "g"
    assert rows["carrot"]["amount"].endswith("g")
    assert "(300ml)" in plan.shopping_list(DB)


# Against the real data: the things that prompted this


@pytest.mark.parametrize(
    "ing_id", ["soy_sauce", "olive_oil", "plant_milk", "vegetable_stock"]
)
def test_everyday_liquids_are_tagged(ing_id):
    assert INGREDIENTS[ing_id].liquid


@pytest.mark.parametrize("ing_id", ["tahini", "peanut_butter", "miso", "carrot"])
def test_thick_things_stay_in_grams(ing_id):
    assert not INGREDIENTS[ing_id].liquid


def test_dashboard_slot_items_carry_a_display_amount():
    soy = INGREDIENTS["soy_sauce"]
    item = _slot_item("soy_sauce", 12 * soy.density)
    assert item["amount"] == "12ml"
    assert item["unit"] == "ml"
    assert item["grams"] == pytest.approx(12 * soy.density)
    assert _slot_item("carrot", 80)["amount"] == "80g"
    assert _slot_item("carrot", None)["amount"] is None


def test_dashboard_meta_gives_unit_and_density():
    rows = {r["id"]: r for r in meta()["ingredients"]}
    assert rows["soy_sauce"]["unit"] == "ml"
    assert rows["soy_sauce"]["density"] == INGREDIENTS["soy_sauce"].density
    assert rows["carrot"]["unit"] == "g"
    assert rows["carrot"]["density"] is None
