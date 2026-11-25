from datetime import date

import pytest

from garde import pantry
from garde.pantry import Item
from garde.planner import parse_inventory


START = date(2026, 12, 1)


def test_round_trip(tmp_path):
    path = tmp_path / "pantry.json"
    items = [Item("spinach", "1", "2026-12-02"), Item("red_lentils", "500g")]
    pantry.save(items, path)
    assert pantry.load(path) == items


def test_missing_file_is_empty(tmp_path):
    assert pantry.load(tmp_path / "nope.json") == []


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
