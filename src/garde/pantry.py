"""
garde.pantry
=============
What's in the kitchen, kept between visits: the dashboard's Pantry tab.

Stored as JSON at $GARDE_HOME/pantry.json (default ~/.garde):

    {"items": [{"id": "spinach", "amount": "1", "use_by": "2026-11-27"}, ...]}

`amount` is what the planner's inventory takes: a count ("3" carrots) or a
measure ("500g"). `use_by` is the last day it's good, or null for "the usual
shelf life from the plan's start" (tins, dried things). Two items with the same
id are two separate lots, say an open block of tofu and a fresh one.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from garde.loader import INGREDIENTS
from garde.models import Ingredient
from garde.planner import InventoryEntry
from garde.planner import parse_amount
from garde.utils import garde_home


def default_path() -> Path:
    return garde_home() / "pantry.json"


@dataclass(frozen=True)
class Item:
    id: str
    amount: str
    use_by: str | None = None  # ISO date, last good day

    def use_by_date(self) -> date | None:
        return date.fromisoformat(self.use_by) if self.use_by else None


def validate(raw: Any, db: dict[str, Ingredient] | None = None) -> list[Item]:
    """Items from untrusted JSON. Raises ValueError with a readable message."""
    db = db or INGREDIENTS
    if not isinstance(raw, list):
        raise ValueError("items must be a list")
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each item must be an object")
        ing_id = entry.get("id")
        if ing_id not in db:
            raise ValueError(f"unknown ingredient {ing_id!r}")
        amount = str(entry.get("amount") or "").strip()
        if not amount:
            continue  # a row still being filled in
        parse_amount(db[ing_id], _measured(db[ing_id], amount))
        use_by = entry.get("use_by") or None
        if use_by is not None:
            try:
                date.fromisoformat(use_by)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{ing_id}: use_by must be a date") from e
        items.append(Item(ing_id, amount, use_by))
    return items


def _measured(ing: Ingredient, amount: str) -> str | float:
    """A bare number is a count if the thing has a natural unit, else grams."""
    try:
        number = float(amount)
    except ValueError:
        return amount
    return number if ing.unit_weight else f"{amount}g"


def load(path: Path | None = None) -> list[Item]:
    path = path or default_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return validate(raw.get("items", []))


def save(items: list[Item], path: Path | None = None) -> Path:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": [asdict(i) for i in items]}, indent=1))
    tmp.replace(path)
    return path


def to_inventory(
    items: list[Item],
    start: date,
    db: dict[str, Ingredient] | None = None,
) -> dict[str, list[InventoryEntry]]:
    """
    The planner's inventory for a plan starting on `start` (day 1). Things past
    their use-by date are left out.
    """
    db = db or INGREDIENTS
    out: dict[str, list[InventoryEntry]] = {}
    for item in items:
        amount = _measured(db[item.id], item.amount)
        use_by = item.use_by_date()
        if use_by is None:
            out.setdefault(item.id, []).append(amount)
            continue
        if use_by < start:
            continue
        expires = (use_by - start).days + 1
        out.setdefault(item.id, []).append({"amount": amount, "expires": expires})
    return out
