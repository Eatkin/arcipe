import random
from pathlib import Path

import pytest

from arcipe.loader import RECIPES
from arcipe.resolver import resolve
from arcipe.solver import optimise_week
from arcipe.vibes import Feedback


def fb(*ratings) -> Feedback:
    f = Feedback(recipes=RECIPES)
    for recipe_id, ingredients, rating in ratings:
        f.add(recipe_id, ingredients, rating)
    return f


def test_scores_shrink_towards_neutral():
    f = fb(("rajma", ["kidney_beans"], -1))
    assert f.recipe_score("rajma") == pytest.approx(-1 / 3)
    f = fb(*[("rajma", ["kidney_beans"], -1)] * 3)
    assert f.recipe_score("rajma") == pytest.approx(-3 / 5)
    assert f.recipe_score("saag") == 0


def test_affinity_generalises_within_cuisine_family_only():
    f = fb(("carrot_curry_gravy", ["roasted_carrot"], 1))
    same = f.ingredient_affinity(RECIPES["carrot_curry_gravy"])["roasted_carrot"]
    family = f.ingredient_affinity(RECIPES["rajma"])["roasted_carrot"]  # also indian
    other = f.ingredient_affinity(RECIPES["green_pasta"])  # italian
    assert same > family > 0
    assert "roasted_carrot" not in other


def test_rating_must_be_thumbs():
    with pytest.raises(ValueError):
        Feedback().add("rajma", [], 5)


def test_round_trips_through_yaml(tmp_path: Path):
    path = tmp_path / "fb.yaml"
    fb(("rajma", ["tomato", "kidney_beans"], 1)).save(path)
    loaded = Feedback.load(path, RECIPES)
    assert loaded.recipe_score("rajma") == pytest.approx(1 / 3)


def test_missing_file_is_empty(tmp_path: Path):
    assert Feedback.load(tmp_path / "nope.yaml").ratings == []


def test_disliked_ingredient_loses_a_close_call():
    stock = {"spinach": 400.0, "kale": 400.0}
    base = resolve(RECIPES["saag"], stock, servings=2, rng=random.Random(0))
    first = next(a for a in base.assignments if a.slot_id == "saag_green").assigned[0]
    f = fb(*[("saag", [first], -1)] * 4)
    r = resolve(
        RECIPES["saag"],
        stock,
        servings=2,
        rng=random.Random(0),
        affinity=f.ingredient_affinity(RECIPES["saag"]),
    )
    assert (
        next(a for a in r.assignments if a.slot_id == "saag_green").assigned[0] != first
    )


def test_solver_avoids_a_disliked_recipe():
    inventory = {"potato": 4, "carrot": 6}
    plain = optimise_week(
        inventory=inventory, days=3, iterations=300, rng=random.Random(0)
    )
    cooked = {s.recipe_id for s in plain.plan.slots if s.recipe_id}
    victim = sorted(cooked)[0]
    f = fb(*[(victim, [], -1)] * 6)
    steered = optimise_week(
        inventory=inventory, days=3, iterations=300, rng=random.Random(0), feedback=f
    )
    assert victim not in {s.recipe_id for s in steered.plan.slots}
