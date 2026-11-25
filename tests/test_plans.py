import copy
from datetime import date
from datetime import datetime

import pytest

from garde import pantry
from garde import plans
from garde.dashboard import plan_request
from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.pantry import Dish
from garde.pantry import Item
from garde.pantry import Pantry
from garde.plans import PlanError
from garde.vibes import Feedback


COOK_DAY = date(2026, 12, 1)
DRIED = INGREDIENTS["chickpeas"].made_from


def _meal(meal="dinner", **kw):
    return {"meal": meal, "leftovers_from": None, "recipe_id": None, **kw}


def _result():
    """A two-day plan: soup for four on day 1, the other half on day 2."""
    soup = _meal(
        recipe_id="lentil_soup",
        recipe_name="Lentil Soup",
        servings=4,
        consumed={"red_lentils": 150.0, "carrot": 200.0, "dried_chickpeas": 84.0},
        made={"chickpeas": 200.0},
    )
    leftovers = {**soup, "leftovers_from": 1, "leftovers_from_date": "2026-12-01"}
    return {
        "plan": {
            "days": [
                {"day": 1, "date": "2026-12-01", "meals": [soup, _meal("lunch")]},
                {"day": 2, "date": "2026-12-02", "meals": [leftovers]},
            ]
        },
        "breakdown": {"objective": 1.0},
    }


def _shopping():
    result = _result()
    result["plan"]["shopping"] = [
        {"id": "tofu", "name": "Tofu", "packs": 2, "grams": 560},
        {"id": "carrot", "name": "Carrot", "packs": 1, "grams": 500},
        {"id": "red_lentils", "name": "Red lentils", "packs": 1, "grams": 500},
    ]
    return result


@pytest.fixture
def root(tmp_path):
    return tmp_path / "plans"


@pytest.fixture
def stock():
    return Pantry(
        items=[
            Item("red_lentils", "500g"),
            Item("carrot", "6", "2026-12-05"),
            Item("dried_chickpeas", "500g"),
        ]
    )


def _grams(stock, ing_id):
    return sum(pantry.grams(i) for i in stock.items if i.id == ing_id)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def test_save_list_load_delete(root):
    when = datetime(2026, 11, 25, 18, 30, 12)
    saved = plans.save(_result(), {"people": 2}, now=when, root=root)
    assert saved["id"].startswith("20261125-183012-")
    assert saved["name"] == "Week from Tue 1 Dec"
    assert saved["start_date"] == "2026-12-01"
    assert plans.load(saved["id"], root) == saved
    (listed,) = plans.list_plans(root)
    assert listed == {
        "id": saved["id"],
        "name": "Week from Tue 1 Dec",
        "saved_at": "2026-11-25T18:30:12",
        "start_date": "2026-12-01",
        "days": 2,
        "meals": 2,
        "cooked": 0,
        "to_buy": 0,
        "bought": 0,
    }
    plans.delete(saved["id"], root)
    assert plans.list_plans(root) == []


def test_newest_first(root):
    a = plans.save(_result(), now=datetime(2026, 11, 1), root=root)
    b = plans.save(_result(), name="Christmas", now=datetime(2026, 12, 1), root=root)
    assert [p["id"] for p in plans.list_plans(root)] == [b["id"], a["id"]]
    assert plans.list_plans(root)[0]["name"] == "Christmas"


def test_plan_ids_cant_reach_outside_the_folder(root):
    for bad in ("../pantry", "x", "20261125-183012-ab12/../../x"):
        with pytest.raises(PlanError, match="no such plan"):
            plans.load(bad, root)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(plan=None), "days"),
        (lambda r: r["plan"]["days"][0].update(day=3), "numbered"),
        (lambda r: r["plan"]["days"][0].update(date="soon"), "date"),
        (lambda r: r["plan"]["days"][0]["meals"][0].update(recipe_id="nope"), "recipe"),
        (
            lambda r: r["plan"]["days"][0]["meals"][0].update(consumed={"unicorn": 3}),
            "unicorn",
        ),
    ],
)
def test_save_rejects_bad_plans(root, change, message):
    result = _result()
    change(result)
    with pytest.raises(PlanError, match=message):
        plans.save(result, root=root)


# ---------------------------------------------------------------------------
# Ticking off
# ---------------------------------------------------------------------------


def _saved(root):
    return plans.save(_result(), {"people": 2}, root=root)


def test_cooking_takes_ingredients_out(root, stock):
    record, after = plans.cook(_saved(root), 1, "dinner", stock, when=COOK_DAY)
    assert _grams(after, "red_lentils") == pytest.approx(350)
    assert _grams(after, "carrot") == pytest.approx(
        _grams(stock, "carrot") - 200, abs=1
    )
    meal = plans.find_meal(record, 1, "dinner")
    assert meal["cooked"]["on"] == "2026-12-01"


def test_cooking_a_pot_of_beans_keeps_the_rest_of_the_pot(root, stock):
    _, after = plans.cook(_saved(root), 1, "dinner", stock, when=COOK_DAY)
    rest = DRIED.batch - 200  # cooked grams beyond what the soup took
    (beans,) = [i for i in after.items if i.id == "chickpeas"]
    assert pantry.grams(beans) == pytest.approx(rest, abs=1)
    assert beans.use_by == "2026-12-05"  # keeps_days 4
    used = 84 + rest * DRIED.ratio
    assert _grams(after, "dried_chickpeas") == pytest.approx(500 - used, abs=1)


def test_spare_portions_go_in_as_a_dish(root, stock):
    _, after = plans.cook(_saved(root), 1, "dinner", stock, when=COOK_DAY)
    (dish,) = after.dishes
    assert dish.recipe_id == "lentil_soup"
    assert dish.portions == 2
    assert dish.cooked == "2026-12-01"
    assert dish.use_by == "2026-12-04"  # keeps_days 3
    assert dish.grams_per_portion == pytest.approx((150 + 200 + 84) / 4, abs=0.1)


def test_a_dish_that_doesnt_keep_isnt_stored(root, stock):
    record = _saved(root)
    meal = plans.find_meal(record, 1, "dinner")
    meal["recipe_id"] = next(r for r, rec in RECIPES.items() if rec.keeps_days == 0)
    _, after = plans.cook(record, 1, "dinner", stock, when=COOK_DAY)
    assert after.dishes == []


def test_eating_leftovers_takes_portions_not_ingredients(root, stock):
    record = _saved(root)
    record, after = plans.cook(record, 1, "dinner", stock, when=COOK_DAY)
    record, later = plans.cook(record, 2, "dinner", after, when=date(2026, 12, 2))
    assert later.items == after.items
    assert later.dishes == []


def test_untick_puts_everything_back(root, stock):
    record = _saved(root)
    record, after = plans.cook(record, 1, "dinner", stock, when=COOK_DAY)
    record, back = plans.cook(record, 1, "dinner", after, cooked=False)
    assert plans.find_meal(record, 1, "dinner")["cooked"] is None
    assert back.dishes == []
    assert {i.id for i in back.items} == {i.id for i in stock.items}
    for item in stock.items:
        assert _grams(back, item.id) == pytest.approx(pantry.grams(item), abs=1)


def test_untick_leftovers_puts_the_portions_back(root, stock):
    record = _saved(root)
    record, after = plans.cook(record, 1, "dinner", stock, when=COOK_DAY)
    record, eaten = plans.cook(record, 2, "dinner", after)
    _, back = plans.cook(record, 2, "dinner", eaten, cooked=False)
    assert back.dishes == after.dishes


def test_ticking_twice_does_nothing_more(root, stock):
    record = _saved(root)
    record, once = plans.cook(record, 1, "dinner", stock, when=COOK_DAY)
    _, twice = plans.cook(copy.deepcopy(record), 1, "dinner", once, when=COOK_DAY)
    assert twice == once


def test_cant_tick_an_empty_or_missing_meal(root, stock):
    record = _saved(root)
    with pytest.raises(PlanError, match="nothing was planned"):
        plans.cook(record, 1, "lunch", stock)
    with pytest.raises(PlanError, match="no breakfast"):
        plans.cook(record, 1, "breakfast", stock)


def test_cook_and_save_writes_both(tmp_path, root, stock):
    pantry_path = tmp_path / "pantry.json"
    pantry.save(stock, pantry_path)
    saved = _saved(root)
    plans.cook_and_save(
        saved["id"], 1, "dinner", when=COOK_DAY, root=root, pantry_path=pantry_path
    )
    assert plans.list_plans(root)[0]["cooked"] == 1
    assert pantry.load(pantry_path).dishes[0].recipe_id == "lentil_soup"


# ---------------------------------------------------------------------------
# With a real plan
# ---------------------------------------------------------------------------


def test_a_real_plan_ticks_off_from_its_own_numbers(root):
    stock = Pantry(
        items=[
            Item("red_lentils", "500g"),
            Item("carrot", "6", "2026-12-05"),
            Item("potato", "6"),
            Item("spinach", "1", "2026-12-02"),
        ]
    )
    inventory = pantry.to_inventory(stock.items, COOK_DAY)
    body = {
        "inventory": inventory,
        "days": 2,
        "effort": 1,
        "seed": 1,
        "start_date": "2026-12-01",
    }
    result = plan_request(body, Feedback(recipes=RECIPES))
    record = plans.save(result, body, root=root)
    meal = next(m for m in record["plan"]["days"][0]["meals"] if m["recipe_id"])
    _, after = plans.cook(record, 1, meal["meal"], stock, when=COOK_DAY)
    for ing_id, used in meal["consumed"].items():
        if any(i.id == ing_id for i in stock.items):
            expected = max(_grams(stock, ing_id) - used, 0)
            assert _grams(after, ing_id) == pytest.approx(expected, abs=1.5)


def test_pantry_leftovers_get_planned(tmp_path):
    from garde.solver import optimise_week

    left = pantry.to_leftovers([Dish("lentil_soup", 2, use_by="2026-12-02")], COOK_DAY)
    result = optimise_week(days=1, leftovers=left, iterations=50, restarts=1)
    (slot,) = result.plan.slots
    assert slot.recipe_id == "lentil_soup"
    assert slot.leftovers_from is not None


# ---------------------------------------------------------------------------
# Shopping
# ---------------------------------------------------------------------------


def test_bought_goes_in_the_pantry_with_its_shelf_life(root, stock):
    record = plans.save(_shopping(), root=root)
    record, after = plans.shop(record, "tofu", stock, when=COOK_DAY)
    assert Item("tofu", "2", "2026-12-07") in after.items  # 7 days, today is the 1st
    record, after = plans.shop(record, "red_lentils", after, when=COOK_DAY)
    assert _grams(after, "red_lentils") == 1000  # onto the 500g already there
    assert plans.summary(record)["bought"] == 2
    assert plans.summary(record)["to_buy"] == 3


def test_a_pack_that_isnt_whole_units_goes_in_as_grams(root, stock):
    record = plans.save(_shopping(), root=root)
    _, after = plans.shop(record, "carrot", Pantry(), when=COOK_DAY)
    assert after.items == [Item("carrot", "500g", "2026-12-21")]


def test_untick_bought_takes_it_back_out(root, stock):
    record = plans.save(_shopping(), root=root)
    record, after = plans.shop(record, "tofu", stock, when=COOK_DAY)
    record, back = plans.shop(record, "tofu", after, bought=False)
    assert back == stock
    assert plans.summary(record)["bought"] == 0


def test_shop_only_what_is_on_the_list(root, stock):
    record = plans.save(_shopping(), root=root)
    with pytest.raises(PlanError, match="isn't on the shopping list"):
        plans.shop(record, "spinach", stock)


def test_extras_go_in_and_come_back_out(root, stock):
    record = plans.save(_result(), root=root)
    record, after = plans.add_extra(record, "spinach", "1", stock, when=COOK_DAY)
    assert Item("spinach", "1", "2026-12-04") in after.items
    record, after = plans.add_extra(
        record, "red_lentils", "250g", after, use_by="2027-06-01", when=COOK_DAY
    )
    assert [e["id"] for e in record["extras"]] == ["spinach", "red_lentils"]
    record, back = plans.remove_extra(record, 0, after)
    record, back = plans.remove_extra(record, 0, back)
    assert back == stock
    assert record["extras"] == []


@pytest.mark.parametrize(
    ("ing_id", "amount", "message"),
    [
        ("unicorn", "1", "unknown"),
        ("red_lentils", "", "how much"),
        ("red_lentils", "lots", "parse"),
    ],
)
def test_bad_extras(root, stock, ing_id, amount, message):
    record = plans.save(_result(), root=root)
    with pytest.raises(PlanError, match=message):
        plans.add_extra(record, ing_id, amount, stock)


def test_bad_shopping_list_is_refused(root):
    result = _shopping()
    result["plan"]["shopping"][0]["packs"] = "two"
    with pytest.raises(PlanError, match="packs"):
        plans.save(result, root=root)
