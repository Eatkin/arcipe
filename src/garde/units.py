"""
garde.units
===========
Amounts as people read them. Everything is grams inside; liquids
(`Ingredient.density` set) are shown in ml, or litres from a litre up:

    format_amount(soy_sauce, 18)      -> "15ml"
    format_amount(plant_milk, 1250)   -> "1.25l"
    format_amount(carrot, 240)        -> "240g"

`stock_amount` writes the same thing in a form `planner.parse_amount` reads back
(always ml, never l), for amounts saved in the pantry.
"""

from __future__ import annotations

from typing import Literal

from garde.models import Ingredient


Unit = Literal["g", "ml"]


def unit(ing: Ingredient | None) -> Unit:
    return "ml" if ing is not None and ing.density is not None else "g"


def _number(x: float, digits: int = 0) -> str:
    """No trailing zeros, no exponent: 1.5, 12, 0.3."""
    if digits == 0:
        return str(round(x))
    return f"{x:.{digits}f}".rstrip("0").rstrip(".")


def format_amount(ing: Ingredient | None, grams: float) -> str:
    """Like 15ml, 1.2l or 250g. Unknown ingredients (prepared forms) are grams."""
    if ing is None or ing.density is None:
        return f"{_number(grams)}g"
    ml = grams / ing.density
    if round(ml) >= 1000:
        return f"{_number(ml / 1000, 2)}l"
    if ml < 10:
        return f"{_number(ml, 1)}ml"
    return f"{_number(ml)}ml"


def stock_amount(ing: Ingredient, grams: float, digits: int = 0) -> str:
    """A measured amount for the pantry: "350ml" for liquids, else "350g"."""
    if ing.density is None:
        return f"{_number(grams, digits)}g"
    return f"{_number(grams / ing.density, digits)}ml"
