import random

import pytest

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS
from garde.loader import RECIPES
from garde.loader import SLOTS
from garde.models import Recipe
from garde.resolver import resolve
from garde.resolver import reuse_keys


EVERYTHING = sorted(INGREDIENTS)
SEEDS = range(25)


def slot_of(resolution, slot_id):
    return next(a for a in resolution.assignments if a.slot_id == slot_id)


@pytest.mark.parametrize("recipe_id", sorted(RECIPES))
@pytest.mark.parametrize("seed", SEEDS)
def test_invariants_hold_with_everything_in_stock(recipe_id, seed):
    recipe = RECIPES[recipe_id]
    r = resolve(recipe, EVERYTHING, rng=random.Random(seed))
    slots = {s.id: s for s in recipe.slots}

    claimed: list[str] = []
    for a in r.assignments:
        slot = slots[a.slot_id]
        assert len(a.assigned) <= slot.max_ingredients
        for ing_id in a.assigned:
            prep = PREPARED_INGREDIENTS.get(ing_id)
            if prep:
                assert prep.method in slot.required_method, (ing_id, slot.id)
            claimed.extend(reuse_keys(ing_id, INGREDIENTS, PREPARED_INGREDIENTS))
    if recipe.no_ingredient_reuse:
        assert len(claimed) == len(set(claimed)), f"reused: {claimed}"
    for req in recipe.required_ingredients:
        assert req in r.used


def test_required_ingredient_is_placed_in_a_slot():
    r = resolve(RECIPES["cauliflower_madras"], ["cauliflower", "chickpeas"])
    assert r.success
    assert "cauliflower" in slot_of(r, "curry_bulk").assigned


def test_missing_required_ingredient_fails():
    r = resolve(RECIPES["cauliflower_madras"], ["chickpeas"])
    assert not r.success
    assert r.missing_required == ["cauliflower"]


def test_required_prepared_ingredient_counts_as_available():
    # katsu requires roux = plain_flour (unlimited) + butter
    without = resolve(RECIPES["katsu_curry"], ["apple", "tofu"])
    assert without.missing_required == ["roux"]
    with_butter = resolve(RECIPES["katsu_curry"], ["apple", "tofu", "butter"])
    assert "roux" in with_butter.used


def test_unplaceable_required_ingredient_is_reported():
    recipe = RECIPES["rajma"].model_copy(
        update={"required_ingredients": ["maple_syrup"]}
    )
    r = resolve(recipe, ["kidney_beans", "maple_syrup"])
    assert r.unplaceable_required == ["maple_syrup"]
    assert not r.success


@pytest.mark.parametrize("seed", SEEDS)
def test_ingredient_and_its_prepared_form_never_both_used(seed):
    r = resolve(
        RECIPES["carrot_curry_gravy"], ["carrot", "potato"], rng=random.Random(seed)
    )
    assert not {"carrot", "roasted_carrot"} <= r.used


def test_prepared_form_consumes_its_bases():
    recipe = RECIPES["katsu_curry"]
    r = resolve(recipe, ["apple", "tofu", "butter"], servings=2)
    # 30g roux per serving, half butter half flour
    assert r.consumed["butter"] == pytest.approx(30)
    assert r.consumed["plain_flour"] >= 30
    assert "roux" not in r.consumed


def _flatbread_and_roux() -> Recipe:
    return Recipe(
        id="test",
        name="Test",
        description="flatbread base with a roux sauce",
        slots=[SLOTS["curry_grain_base"], SLOTS["katsu_sauce_body"]],
        edges=[],
        required_ingredients=["flatbread", "roux"],
    )


def test_staple_can_be_shared_between_prepared_forms():
    r = resolve(_flatbread_and_roux(), ["butter"], servings=2)
    assert r.success, r.summary()
    # 360g flatbread at 0.6 flour + 60g roux at 0.5 flour
    assert r.consumed["plain_flour"] == pytest.approx(246)


def test_non_staple_cannot_be_shared_between_prepared_forms():
    db = dict(INGREDIENTS)
    db["plain_flour"] = db["plain_flour"].model_copy(update={"staple": False})
    r = resolve(_flatbread_and_roux(), ["butter"], ingredient_db=db)
    assert r.unplaceable_required == ["roux"]


@pytest.mark.parametrize("seed", SEEDS)
def test_pasta_goes_on_pasta(seed):
    r = resolve(RECIPES["green_pasta"], ["spinach", "cashews"], rng=random.Random(seed))
    (base,) = slot_of(r, "grain_base").assigned
    assert "pasta" in INGREDIENTS[base].classes


@pytest.mark.parametrize("seed", SEEDS)
def test_roasted_carrot_never_in_wilted_greens(seed):
    r = resolve(
        RECIPES["creamy_tuscan_beans"],
        ["carrot", "cannellini_beans", "mushrooms"],
        rng=random.Random(seed),
    )
    assert "roasted_carrot" not in slot_of(r, "stew_wilted_greens").assigned


def test_perishable_preferred_over_equivalent_non_perishable():
    # fresh spinach (perishable) beats frozen spinach for the saag green
    r = resolve(RECIPES["saag"], ["spinach", "frozen_spinach", "potato"])
    assert slot_of(r, "saag_green").assigned[0] == "spinach"


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------


def test_not_enough_of_anything_leaves_slot_unfillable():
    # saag needs 400g of greens for 2; 100g of spinach won't do
    r = resolve(RECIPES["saag"], {"spinach": 100.0, "potato": 1000.0}, servings=2)
    assert "saag_green" in r.unfillable


def test_slot_combines_odds_and_ends_to_reach_its_amount():
    r = resolve(RECIPES["saag"], {"spinach": 200.0, "kale": 200.0}, servings=2)
    greens = slot_of(r, "saag_green")
    assert greens.filled
    assert set(greens.assigned) == {"spinach", "kale"}
    assert sum(greens.grams.values()) == pytest.approx(400)


def test_optional_slot_that_cant_be_covered_consumes_nothing():
    r = resolve(RECIPES["saag"], {"spinach": 400.0, "coriander_leaf": 1.0}, servings=2)
    assert slot_of(r, "finish_herb").assigned == []
    assert "coriander_leaf" not in r.consumed


def test_dried_ingredients_consume_dry_weight():
    r = resolve(RECIPES["rajma"], {"red_lentils": 1000.0}, servings=2)
    protein = slot_of(r, "curry_protein")
    if "red_lentils" in protein.assigned:
        in_dish = protein.grams["red_lentils"]
        assert r.consumed["red_lentils"] == pytest.approx(in_dish * 0.4)


def test_prepared_form_consumes_bases_by_ratio():
    db = dict(INGREDIENTS)
    # make cashew cream the only fat option
    recipe = Recipe(
        id="t",
        name="T",
        description="",
        slots=[SLOTS["creamy_curry_fat"]],
        edges=[],
        required_ingredients=["cashew_cream"],
    )
    r = resolve(recipe, {"cashews": 500.0}, ingredient_db=db, servings=2)
    assert slot_of(r, "creamy_curry_fat").grams["cashew_cream"] == pytest.approx(200)
    assert r.consumed["cashews"] == pytest.approx(80)


def test_strong_flavours_are_capped_per_serving():
    r = resolve(RECIPES["rajma"], ["kidney_beans"], servings=2)
    aromatics = slot_of(r, "curry_aromatic").grams
    assert aromatics.get("garlic", 0) <= 16
    assert sum(aromatics.values()) == pytest.approx(160)


def test_tracked_unlimited_ingredient_can_run_out():
    r = resolve(RECIPES["katsu_curry"], {"butter": 100.0, "plain_flour": 0.0})
    assert r.missing_required == ["roux"]


def test_urgency_prefers_what_expires_soonest():
    stock = {"spinach": 400.0, "kale": 400.0}
    soon_kale = resolve(
        RECIPES["saag"], stock, servings=2, urgency={"kale": 1.0, "spinach": 0.0}
    )
    assert slot_of(soon_kale, "saag_green").assigned[0] == "kale"
    soon_spinach = resolve(
        RECIPES["saag"], stock, servings=2, urgency={"kale": 0.0, "spinach": 1.0}
    )
    assert slot_of(soon_spinach, "saag_green").assigned[0] == "spinach"


@pytest.mark.parametrize("recipe_id", sorted(RECIPES))
def test_every_recipe_is_makeable_with_everything_in_stock(recipe_id):
    r = resolve(RECIPES[recipe_id], EVERYTHING, servings=2, rng=random.Random(0))
    assert r.success, r.summary()
