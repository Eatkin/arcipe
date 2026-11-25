import random

import pytest

from garde.loader import RECIPES
from garde.solver import Weights
from garde.solver import optimise_week
from garde.solver import weights_from


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


# The original 14-recipe book, where few recipes use each perishable — greedy
# walks into waste that looking ahead avoids
SMALL_BOOK = [
    "crumpet_meal",
    "vegan_banana_bread",
    "rajma",
    "butter_chickpeas",
    "cauliflower_madras",
    "vindaloo",
    "saag",
    "katsu_curry",
    "carrot_curry_gravy",
    "green_pasta",
    "green_grain_bowl",
    "green_flatbread",
    "creamy_tomato_lentil",
    "creamy_tuscan_beans",
]


def test_beats_greedy_on_waste_when_looking_ahead_matters():
    book = {rid: RECIPES[rid] for rid in SMALL_BOOK}
    result = _solve(0, iterations=1500, recipes=book)
    assert result.breakdown.waste_g < result.greedy.waste_g


def test_beats_greedy_overall_on_the_full_book():
    result = _solve(0, iterations=1500)
    assert result.breakdown.objective < result.greedy.objective


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


def test_per_day_time_limit_only_binds_its_day():
    limits = [20, None, 20, None, 20, None, 20]
    result = _solve(days=7, max_minutes=limits)
    for slot in result.plan.slots:
        limit = limits[slot.day - 1]
        if slot.resolution and slot.leftovers_from is None and limit is not None:
            assert slot.resolution.timing.total <= limit


def test_progress_reports_count_up_to_total():
    seen = []
    _solve(
        iterations=120,
        restarts=2,
        progress=lambda done, total, tried, best: seen.append((done, total, tried)),
    )
    assert seen, "progress never called"
    assert [d for d, _, _ in seen] == sorted(d for d, _, _ in seen)
    assert seen[-1][0] == seen[-1][1] == 240
    assert all(tried >= 1 for _, _, tried in seen)


def test_progress_can_stop_the_search():
    class StopError(Exception):
        pass

    def stop(done, total, tried, best):
        raise StopError

    with pytest.raises(StopError):
        _solve(iterations=200, progress=stop)


# ---------------------------------------------------------------------------
# Why a meal is empty
# ---------------------------------------------------------------------------

_DRIED = {
    "apple": "900g",
    "banana": "240g",
    "brussels_sprouts": "500g",
    "dried_butter_beans": "900g",
    "dried_chickpeas": "900g",
    "dried_kidney_beans": "900g",
}


def _why(**kw):
    from garde.solver import optimise_week

    result = optimise_week(iterations=30, restarts=1, rng=random.Random(0), **kw)
    return result.plan.unplanned, result.reasons


def test_too_tight_a_time_limit_says_so():
    unplanned, reasons = _why(inventory=_DRIED, days=2, max_minutes=15)
    assert unplanned == [(1, "dinner"), (2, "dinner")]
    assert "quickest dinner you could make takes" in reasons[(1, "dinner")]
    assert "over the 15 min limit" in reasons[(1, "dinner")]


def test_an_empty_pantry_suggests_shopping():
    unplanned, reasons = _why(inventory={}, days=1)
    assert unplanned == [(1, "dinner")]
    assert "Turn on shopping" in reasons[(1, "dinner")]


def test_quickest_minutes_per_meal():
    from garde.solver import quickest_minutes

    q = quickest_minutes()
    assert 0 < q["breakfast"] <= q["dinner"] <= 60
