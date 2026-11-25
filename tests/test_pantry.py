from datetime import date

import pytest

from garde import pantry
from garde.loader import INGREDIENTS
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


def test_binning_logs_real_waste(tmp_path):
    from garde.pantry import bin_things
    from garde.pantry import load_waste
    from garde.pantry import wasted_since

    path = tmp_path / "waste.json"
    stock = Pantry(
        [Item("spinach", "2", "2026-11-20"), Item("carrot", "500g")],
        [Dish("chilli", 3, "2026-11-24", "2026-11-21", 300)],
    )
    when = date(2026, 11, 25)
    stock, log = bin_things(
        stock,
        [Item("spinach", "1", "2026-11-20")],
        [Dish("chilli", 1, "2026-11-24", "2026-11-21")],
        when,
        path=path,
    )
    assert [i.amount for i in stock.items if i.id == "spinach"] == ["1"]
    assert stock.dishes[0].portions == 2
    assert [e["grams"] for e in load_waste(path)] == [
        round(pantry.grams(Item("spinach", "1"))),
        300,
    ]
    assert wasted_since(date(2026, 11, 1), path) == sum(e["grams"] for e in log)
    assert wasted_since(date(2026, 11, 26), path) == 0
    with pytest.raises(ValueError, match="isn't in the pantry"):
        bin_things(stock, [Item("tofu", "1")], [], when, path=path)


def test_household_staples_not_kept(tmp_path):
    path = tmp_path / "household.json"
    assert pantry.load_household(path) == {"not_kept": []}
    saved = pantry.save_household({"not_kept": ["cashews", "cashews"]}, path)
    assert saved == {"not_kept": ["cashews"]} == pantry.load_household(path)
    with pytest.raises(ValueError, match="isn't one of the staples"):
        pantry.save_household({"not_kept": ["spinach"]}, path)
    db = pantry.kitchen_db(("cashews",))
    assert not db["cashews"].unlimited
    assert db is pantry.kitchen_db(["cashews"])  # same object: resolver caches by it
    assert pantry.kitchen_db(()) is INGREDIENTS


def test_a_staple_not_kept_has_to_be_bought():
    from garde.dashboard import solve_args
    from garde.loader import RECIPES
    from garde.resolver import resolve
    from garde.vibes import Feedback

    args, _ = solve_args(
        {"not_kept": ["cashews"], "inventory": {}}, Feedback(recipes=RECIPES)
    )
    db = args["ingredient_db"]
    full = {i: 10000.0 for i in INGREDIENTS if i != "cashews"}
    uses = [
        r
        for r in RECIPES.values()
        if "cashews" in resolve(r, full | {"cashews": 10000.0}, db).consumed
    ]
    assert uses
    for r in uses:
        assert "cashews" not in resolve(r, full, db).consumed


def test_frozen_dishes_wait_a_night_to_defrost():
    from datetime import timedelta

    today = date.today()
    dish = Dish("bean_chilli", 2, (today + timedelta(days=90)).isoformat(), frozen=True)
    (left,) = pantry.to_leftovers([dish], today)
    assert left.ready_day == 2 and left.frozen
    from garde.planner import eat_leftovers

    assert eat_leftovers([left], "dinner", 2, day=1)[1] is None
    assert eat_leftovers([left], "dinner", 2, day=2)[1] is not None
    raw = Pantry([Item("spinach", "1", frozen=True)], [dish]).to_json()
    assert raw["items"][0]["frozen"] and pantry.validate(raw["items"])[0].frozen
    assert "frozen" not in Pantry([Item("spinach", "1")]).to_json()["items"][0]
