from garde.loader import PREPARED_INGREDIENTS
from garde.loader import RECIPES
from garde.loader import SLOTS
from garde.models import Recipe
from garde.models import RecipeEdge
from garde.models import Time
from garde.resolver import resolve
from garde.timing import has_cycle
from garde.timing import recipe_timing


def _slot(slot_id: str, active: int, passive: int):
    return SLOTS[slot_id].model_copy(
        update={"time": Time(active=active, passive=passive)}
    )


def _recipe(slots, edges, edge_type="combine") -> Recipe:
    return Recipe(
        id="t",
        name="T",
        description="",
        slots=slots,
        edges=[
            RecipeEdge(from_slot=a, to_slot=b, edge_type=edge_type) for a, b in edges
        ],
    )


def test_parallel_steps_take_the_longest_branch():
    # rice (20) runs alongside aromatics (10) -> sauce (15)
    recipe = _recipe(
        [
            _slot("curry_grain_base", 5, 15),
            _slot("curry_aromatic", 10, 0),
            _slot("tomato_sauce_body", 3, 12),
        ],
        [
            ("curry_aromatic", "tomato_sauce_body"),
            ("tomato_sauce_body", "curry_grain_base"),
        ],
    )
    t = recipe_timing(recipe, {}, PREPARED_INGREDIENTS)
    assert t.active == 18
    # aromatics 10 -> sauce 15 -> rice 20 in sequence by the edges
    assert t.total == 45


def test_assembly_edges_cook_in_parallel():
    # sauce (15) coats rice (20): rice boils while the sauce simmers
    recipe = _recipe(
        [_slot("curry_grain_base", 5, 15), _slot("tomato_sauce_body", 3, 12)],
        [("tomato_sauce_body", "curry_grain_base")],
        edge_type="coat",
    )
    assert recipe_timing(recipe, {}, PREPARED_INGREDIENTS).total == 20


def test_independent_steps_overlap():
    recipe = _recipe(
        [_slot("curry_grain_base", 5, 15), _slot("curry_aromatic", 10, 0)], []
    )
    assert recipe_timing(recipe, {}, PREPARED_INGREDIENTS).total == 20


def test_prepared_form_adds_to_its_slot_and_make_ahead_doesnt():
    recipe = _recipe([_slot("root_sauce_body", 5, 0)], [])
    roast = recipe_timing(
        recipe, {"root_sauce_body": ["roasted_carrot"]}, PREPARED_INGREDIENTS
    )
    assert roast.total == 5 + 40

    recipe = _recipe([_slot("creamy_curry_fat", 1, 0)], [])
    soak = recipe_timing(
        recipe, {"creamy_curry_fat": ["cashew_cream"]}, PREPARED_INGREDIENTS
    )
    assert soak.total == 6  # hands-on only; the 2h soak happened earlier
    assert soak.make_ahead == ["cashew_cream"]
    assert soak.active == 6


def test_cycle_detection():
    looped = _recipe(
        [_slot("curry_aromatic", 1, 0), _slot("spice_bloom", 1, 0)],
        [("curry_aromatic", "spice_bloom"), ("spice_bloom", "curry_aromatic")],
    )
    assert has_cycle(looped)


def test_resolution_carries_timing():
    r = resolve(RECIPES["rajma"], ["kidney_beans"], servings=2)
    assert 0 < r.timing.active <= r.timing.total


def test_one_cook_cannot_be_faster_than_their_hands_on_time():
    recipe = _recipe([_slot("curry_aromatic", 10, 0), _slot("spice_bloom", 10, 0)], [])
    t = recipe_timing(recipe, {}, PREPARED_INGREDIENTS)
    assert t.total == 20
