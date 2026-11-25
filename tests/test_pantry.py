from datetime import date

import pytest

from garde import pantry
from garde.pantry import Dish
from garde.pantry import Item
from garde.pantry import Pantry
from garde.planner import parse_inventory


START = date(2026, 12, 1)


def test_round_trip(tmp_path):
    path = tmp_path / "pantry.json"
    stock = Pantry(
        items=[Item("spinach", "1", "2026-12-02"), Item("red_lentils", "500g")],
        dishes=[Dish("lentil_soup", 2, "2026-12-03", "2026-11-30", 250.0)],
    )
    pantry.save(stock, path)
    assert pantry.load(path) == stock


def test_old_file_without_dishes_loads(tmp_path):
    path = tmp_path / "pantry.json"
    path.write_text('{"items": [{"id": "red_lentils", "amount": "500g"}]}')
    assert pantry.load(path) == Pantry([Item("red_lentils", "500g")], [])


def test_missing_file_is_empty(tmp_path):
    assert pantry.load(tmp_path / "nope.json") == Pantry()


def test_default_path_follows_garde_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GARDE_HOME", str(tmp_path))
    assert pantry.default_path() == tmp_path / "pantry.json"


def test_use_by_becomes_days_from_start():
    inv = pantry.to_inventory([Item("spinach", "1", "2026-12-02")], START)
    assert inv == {"spinach": [{"amount": 1.0, "expires": 2}]}


def test_use_by_today_is_good_for_day_one():
    (lot,) = parse_inventory(
        pantry.to_inventory([Item("tofu", "1", "2026-12-01")], START)
    )
    assert lot.expires_day == 1


def test_past_items_are_dropped():
    assert pantry.to_inventory([Item("spinach", "1", "2026-11-30")], START) == {}


def test_two_lots_of_one_thing():
    items = [Item("tofu", "1", "2026-12-02"), Item("tofu", "1", "2026-12-08")]
    lots = parse_inventory(pantry.to_inventory(items, START))
    assert sorted(lot.expires_day for lot in lots) == [2, 8]


def test_bare_number_is_grams_without_a_natural_unit():
    (lot,) = parse_inventory(pantry.to_inventory([Item("red_lentils", "300")], START))
    assert lot.grams == 300


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("nope", "list"),
        ([{"id": "unicorn", "amount": "1"}], "unknown ingredient"),
        ([{"id": "spinach", "amount": "1", "use_by": "soon"}], "date"),
        ([{"id": "red_lentils", "amount": "lots"}], "parse"),
    ],
)
def test_validate_rejects_bad_items(raw, message):
    with pytest.raises(ValueError, match=message):
        pantry.validate(raw)


def test_validate_skips_rows_without_an_amount():
    assert pantry.validate([{"id": "spinach", "amount": ""}]) == []


# ---------------------------------------------------------------------------
# Cooked dishes
# ---------------------------------------------------------------------------


def test_dishes_become_leftovers_from_the_plan_start():
    dishes = [
        Dish("lentil_soup", 2, use_by="2026-12-02", cooked="2026-11-30"),
        Dish("lentil_soup", 2, use_by="2026-11-30", cooked="2026-11-28"),  # gone off
    ]
    (left,) = pantry.to_leftovers(dishes, START)
    assert (left.cooked_day, left.expires_day, left.portions) == (0, 2, 2)
    assert left.grams_per_portion == pantry.PORTION_GRAMS  # unknown, so a guess
    assert "dinner" in left.meal_types
    assert left.resolution.success


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([{"recipe_id": "nope", "portions": 1, "use_by": "2026-12-01"}], "unknown"),
        (
            [{"recipe_id": "lentil_soup", "portions": "x", "use_by": "2026-12-01"}],
            "number",
        ),
        ([{"recipe_id": "lentil_soup", "portions": 1}], "use_by"),
    ],
)
def test_validate_dishes_rejects_bad_ones(raw, message):
    with pytest.raises(ValueError, match=message):
        pantry.validate_dishes(raw)


def test_validate_dishes_drops_eaten_ones():
    raw = [{"recipe_id": "lentil_soup", "portions": 0, "use_by": "2026-12-01"}]
    assert pantry.validate_dishes(raw) == []


def test_eat_soonest_dish_first_across_two():
    dishes = [
        Dish("lentil_soup", 2, use_by="2026-12-04"),
        Dish("lentil_soup", 1, use_by="2026-12-02"),
    ]
    left, eaten = pantry.eat(dishes, "lentil_soup", 2)
    assert left == [Dish("lentil_soup", 1, use_by="2026-12-04")]
    assert [(d.use_by, d.portions) for d in eaten] == [
        ("2026-12-02", 1),
        ("2026-12-04", 1),
    ]


# ---------------------------------------------------------------------------
# Taking grams out and putting them back
# ---------------------------------------------------------------------------


def test_take_soonest_first_and_keeps_counts_as_counts():
    items = [
        Item("tofu", "2"),  # keeps
        Item("tofu", "1", "2026-12-02"),
    ]
    one_block = pantry.grams(Item("tofu", "1"))
    left, taken = pantry.take(items, {"tofu": 1.5 * one_block})
    assert left == [Item("tofu", "1.5")]
    assert taken == [Item("tofu", "1", "2026-12-02"), Item("tofu", "0.5")]


def test_take_skips_what_isnt_there():
    left, taken = pantry.take(
        [Item("red_lentils", "500g")], {"salt": 5, "red_lentils": 120}
    )
    assert left == [Item("red_lentils", "380g")]
    assert taken == [Item("red_lentils", "120g")]


def test_take_everything_drops_the_item():
    left, _ = pantry.take([Item("red_lentils", "100g")], {"red_lentils": 500})
    assert left == []


def test_give_back_merges_or_adds():
    items = [Item("red_lentils", "380g")]
    back = pantry.give(
        items, [Item("red_lentils", "120g"), Item("tofu", "1", "2026-12-02")]
    )
    assert back == [Item("red_lentils", "500g"), Item("tofu", "1", "2026-12-02")]
