"""
Weird picks: ingredients that technically fit a slot's roles but wouldn't really
go in that dish. Each test pins one case that turned up when planning weeks from
a full kitchen, a sparse fridge and with shopping on, so it can't creep back.
"""

import random

import pytest

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.method import steps
from garde.resolver import resolve


# Everything plentiful: every perishable gets the same flat bonus
FULL = list(INGREDIENTS)
# A thin week: a few veg, tins and dried things, plus the unlimited staples
SPARSE = {
    "onion": 1000.0,
    "garlic": 200.0,
    "carrot": 640.0,
    "potato": 1000.0,
    "canned_tomato": 800.0,
    "chickpeas": 480.0,
    "red_lentils": 500.0,
    "cabbage": 800.0,
    "olive_oil": 500.0,
    "plain_flour": 1000.0,
    "lemon": 2.0,
}
PANTRIES = {
    "full": {"inventory": FULL},
    "sparse": {"inventory": SPARSE},
    "shop": {"inventory": SPARSE, "buyable": FULL},
}
SEEDS = range(4)


def picks(recipe_id: str, slot_id: str, **kwargs) -> set[str]:
    """Everything the resolver put in one slot of a recipe, over a few seeds."""
    found: set[str] = set()
    for seed in SEEDS:
        res = resolve(RECIPES[recipe_id], rng=random.Random(seed), **kwargs)
        for a in res.assignments:
            if a.slot_id == slot_id:
                found.update(a.assigned)
    return found


@pytest.fixture(params=sorted(PANTRIES))
def pantry(request) -> dict:
    return PANTRIES[request.param]


# --- the four known cases ---------------------------------------------------


@pytest.mark.parametrize(
    "recipe_id",
    sorted(
        r
        for r, recipe in RECIPES.items()
        if any(s.id == "curry_protein" for s in recipe.slots)
    ),
)
def test_gram_flour_is_never_a_curry_protein(recipe_id, pantry):
    assert "gram_flour" not in picks(recipe_id, "curry_protein", **pantry)


def test_gram_flour_is_never_the_protein_on_a_flatbread(pantry):
    assert "gram_flour" not in picks("green_flatbread", "protein_bulk", **pantry)


def test_chickpea_omelette_still_uses_gram_flour():
    assert picks("chickpea_omelette", "batter_flour", inventory=FULL) == {"gram_flour"}


def test_mayo_is_not_the_cream_in_tuscan_beans(pantry):
    assert "mayo" not in picks("creamy_tuscan_beans", "sauce_creamy_binder", **pantry)


def test_red_lentils_are_not_a_pizza_topping():
    # Even with nothing else to top it with and the lentils about to go off
    stock = {"plain_flour": 1000.0, "canned_tomato": 800.0, "red_lentils": 500.0}
    urgency = {"red_lentils": 1.0}
    for kwargs in ({"inventory": FULL}, {"inventory": stock, "urgency": urgency}):
        assert "red_lentils" not in picks("pizza", "cooked_veg", **kwargs)


def test_tuscan_beans_fry_the_aromatics_first():
    recipe = RECIPES["creamy_tuscan_beans"]
    res = resolve(recipe, FULL, rng=random.Random(0))
    order = [s.split(":")[0] for s in steps(recipe, res)]
    assert order.index("Sautéed Aromatics") < order.index("Chunky Umami Core")
    assert order.index("Chunky Umami Core") < order.index("Creamy Sauce Emulsifier")


# --- found in the same pass ----------------------------------------------------


def test_tuscan_beans_greens_are_leafy(pantry):
    for ing_id in picks("creamy_tuscan_beans", "stew_wilted_greens", **pantry):
        assert "leafy_green" in INGREDIENTS[ing_id].classes


def test_tuscan_beans_are_beans_not_red_lentils(pantry):
    assert "red_lentils" not in picks(
        "creamy_tuscan_beans", "stew_protein_legume", **pantry
    )


def test_tuscan_beans_use_mushrooms_when_there_are_some():
    assert picks("creamy_tuscan_beans", "stew_bulk_umami", inventory=FULL) == {
        "mushrooms"
    }


@pytest.mark.parametrize(
    "recipe_id",
    [
        "buddha_bowl",
        "crispy_bites",
        "fried_rice",
        "grain_salad",
        "noodle_salad",
        "noodle_stir_fry",
        "pasta_salad",
        "potato_salad",
        "raw_salad",
        "saucy_stir_fry",
        "sushi",
        "tray_bake",
    ],
)
def test_red_lentils_stay_out_of_dishes_where_the_protein_keeps_its_shape(
    recipe_id, pantry
):
    # Fried rice, salads, sushi, a roasting tray: lentil mush doesn't work there,
    # even when red lentils are all that's left
    lentils_only = {**SPARSE, "chickpeas": 0.0}
    assert "red_lentils" not in picks(recipe_id, "protein_bulk", **pantry)
    assert "red_lentils" not in picks(recipe_id, "protein_bulk", inventory=lentils_only)


@pytest.mark.parametrize("recipe_id", ["sushi", "green_grain_bowl", "noodle_soup"])
def test_whole_beans_and_tofu_beat_red_lentils_and_peanuts(recipe_id):
    assert not picks(recipe_id, "protein_bulk", inventory=FULL) & {
        "red_lentils",
        "peanuts",
    }


def test_baked_pasta_sauce_is_not_peanut_butter_or_avocado(pantry):
    assert not picks("baked_pasta", "creamy_sauce", **pantry) & {
        "peanut_butter",
        "avocado",
    }


@pytest.mark.parametrize("recipe_id", ["baked_pasta", "green_pasta"])
def test_no_ketchup_in_italian_pasta(recipe_id, pantry):
    assert "tomato_ketchup" not in picks(recipe_id, "umami_boost", **pantry)


def test_tacos_get_no_peanut_butter(pantry):
    assert "peanut_butter" not in picks("tacos", "creamy_drizzle", **pantry)


def test_tacos_spices_are_mexican(pantry):
    assert not picks("tacos", "spice_bloom", **pantry) & {
        "nutmeg",
        "fenugreek_seed",
        "mixed_spice",
    }


@pytest.mark.parametrize(
    "recipe_id",
    sorted(
        r
        for r, recipe in RECIPES.items()
        if any(s.id == "spice_bloom" for s in recipe.slots)
    ),
)
def test_mixed_spice_stays_in_cakes(recipe_id):
    assert "mixed_spice" not in picks(recipe_id, "spice_bloom", inventory=FULL)


@pytest.mark.parametrize("recipe_id", ["butter_chickpeas", "saag"])
def test_raw_sunflower_seeds_are_not_a_creamy_curry_fat(recipe_id, pantry):
    assert "sunflower_seeds" not in picks(recipe_id, "creamy_curry_fat", **pantry)


@pytest.mark.parametrize(
    "recipe_id", ["butter_chickpeas", "cauliflower_madras", "rajma", "vindaloo"]
)
def test_curries_finish_with_coriander(recipe_id):
    assert picks(recipe_id, "finish_herb", inventory=FULL) == {"coriander_leaf"}


def test_stir_fry_acid_is_not_yogurt():
    # Seen in a planned week where the yogurt wanted using up
    urgency = {"plant_yogurt": 0.5}
    assert "plant_yogurt" not in picks(
        "saucy_stir_fry", "acid_bright", inventory=FULL, urgency=urgency
    )


def test_crispy_bites_are_not_served_on_spaghetti(pantry):
    assert not picks("crispy_bites", "hearty_base", **pantry) & {"spaghetti", "pasta"}


@pytest.mark.parametrize("recipe_id", ["pasta_salad", "open_sandwich", "patties"])
def test_celeriac_is_grated_not_sliced_raw(recipe_id, pantry):
    assert "celeriac" not in picks(recipe_id, "raw_veg", **pantry)


def test_saag_aloo_uses_potato():
    assert picks("saag", "starchy_bulk", inventory=FULL) == {"potato"}


# --- veg slots, sauce bodies, pizza (2026-11-25) --------------------------

# Dried beans, fruit and sprouts: the pantry that turned these up
BEANS_AND_FRUIT = {
    "dried_kidney_beans": 1000.0,
    "dried_chickpeas": 1000.0,
    "dried_black_beans": 1000.0,
    "banana": 600.0,
    "apple": 600.0,
    "brussels_sprouts": 800.0,
    "beansprouts": 300.0,
    "carrot": 640.0,
    "spinach": 200.0,
    "red_lentils": 500.0,
    "potato": 1000.0,
    "cabbage": 800.0,
}
PULSES = {i for i, ing in INGREDIENTS.items() if "pulse" in ing.classes}
VEG_SLOTS = {"cooked_veg", "roast_veg", "soup_veg", "grated_veg", "curry_bulk"}


@pytest.mark.parametrize(
    "recipe_id",
    sorted(
        r for r, recipe in RECIPES.items() if {s.id for s in recipe.slots} & VEG_SLOTS
    ),
)
@pytest.mark.parametrize("stock", [BEANS_AND_FRUIT, FULL, SPARSE])
def test_beans_are_not_the_veg_when_there_is_veg(recipe_id, stock):
    for slot in {s.id for s in RECIPES[recipe_id].slots} & VEG_SLOTS:
        assert not picks(recipe_id, slot, inventory=stock) & PULSES, slot


@pytest.mark.parametrize("stock", [BEANS_AND_FRUIT, FULL, SPARSE])
def test_fruit_is_not_a_savoury_sauce_body(stock):
    got = picks("carrot_curry_gravy", "root_sauce_body", inventory=stock)
    assert not got & {"banana", "apple", "orange", "rhubarb", "frozen_fruit"}


@pytest.mark.parametrize("stock", [BEANS_AND_FRUIT, FULL, SPARSE])
def test_pizza_toppings_are_not_roots_cabbage_or_lentils(stock):
    got = picks("pizza", "cooked_veg", inventory=stock)
    assert not got & ({"carrot", "cabbage", "brussels_sprouts"} | PULSES)


@pytest.mark.parametrize("recipe_id", ["veg_soup", "veg_stew", "goulash"])
@pytest.mark.parametrize("stock", [BEANS_AND_FRUIT, FULL, SPARSE])
def test_cabbage_is_not_starchy(recipe_id, stock):
    assert "cabbage" not in picks(recipe_id, "starchy_bulk", inventory=stock)


def test_pulses_still_fill_a_veg_slot_when_nothing_else_will():
    stock = {"kidney_beans": 2000.0, "red_lentils": 500.0, "canned_tomato": 800.0}
    res = resolve(RECIPES["bean_chilli"], stock, rng=random.Random(0))
    assert res.success
