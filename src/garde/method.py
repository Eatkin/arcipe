"""
garde.method
=============
A rough cooking outline for a resolved recipe, built from its slot graph.

Archetypes have no written method, only the shape of the dish: which component
goes into which, and how (EdgeType). This walks that graph in order and says it
in words, with the ingredients the resolver actually picked:

    1. Ahead: cashew cream. Soak 2hrs, drain, blend with water until very smooth.
    2. Roast the carrot (about 40 min). Roast at 200°C until caramelised.
    3. Aromatic base: fry onion, garlic (about 8 min), then stir into the spice bloom.
    ...

It's an outline to cook from alongside a real recipe (Recipe.sources), not one.
"""

from __future__ import annotations

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS
from garde.models import ComponentSlot
from garde.models import EdgeType
from garde.models import Method
from garde.models import PreparedIngredient
from garde.models import Recipe
from garde.resolver import Resolution
from garde.timing import ASSEMBLY_EDGES


# How "from" goes into "to", completing "..., then {phrase}"
EDGE_PHRASE: dict[EdgeType, str] = {
    "combine": "stir into the {to}",
    "fold_in": "fold gently into the {to}",
    "coat": "spoon over the {to}",
    "top": "scatter over the {to}",
    "finish": "add to the {to} at the end",
    "deglaze": "pour into the {to} pan and scrape up the bits",
    "emulsify": "blend into the {to} until smooth",
    "blend": "blend into the {to}",
}

METHOD_VERB: dict[Method, str] = {
    "raw": "prep",
    "blend": "blend",
    "roast": "roast",
    "boil": "boil",
    "steam": "steam",
    "wilt": "wilt",
    "fry": "fry",
    "toast": "toast",
    "pickle": "pickle",
    "ferment": "ferment",
    "soak": "soak",
    "bake": "bake",
}


def display_name(ing_id: str) -> str:
    if ing_id in INGREDIENTS:
        return INGREDIENTS[ing_id].name
    return ing_id.replace("_", " ").capitalize()


def _names(ids: list[str]) -> str:
    names = [display_name(i).lower() for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _order(recipe: Recipe) -> list[str]:
    """
    Slot ids, each after the slots that cook into it, else in recipe order.
    Assembly edges (coat, top, finish) don't count: the rice can go on first.
    """
    feeds: dict[str, list[str]] = {s.id: [] for s in recipe.slots}
    for edge in recipe.edges:
        if edge.edge_type not in ASSEMBLY_EDGES:
            feeds.setdefault(edge.to_slot, []).append(edge.from_slot)
    done: list[str] = []

    def visit(slot_id: str, seen: frozenset[str]) -> None:
        if slot_id in done or slot_id in seen:
            return
        for prev in feeds.get(slot_id, []):
            visit(prev, seen | {slot_id})
        done.append(slot_id)

    for slot in recipe.slots:
        visit(slot.id, frozenset())
    return done


def _verb(slot: ComponentSlot, ids: list[str], prepared: dict) -> str:
    if all(i in prepared for i in ids):
        return "add"
    if slot.amount == "trace":
        return "measure out"
    cooked = [m for m in slot.required_method if m != "raw"]
    if len(cooked) == 1:
        return METHOD_VERB[cooked[0]]
    if not cooked:
        return "prep"
    return "cook"


def _minutes(n: int) -> str:
    return f" (about {n} min)" if n else ""


def _prep_steps(
    used: list[str], prepared: dict[str, PreparedIngredient]
) -> tuple[list[str], list[str]]:
    ahead, now = [], []
    for ing_id in used:
        prep = prepared.get(ing_id)
        if prep is None:
            continue
        note = f" {prep.notes.strip()}" if prep.notes else ""
        if prep.make_ahead:
            ahead.append(f"Ahead: {display_name(ing_id).lower()}.{note}")
        else:
            verb = METHOD_VERB[prep.method].capitalize()
            what = _names(prep.base_ingredients)
            now.append(f"{verb} the {what}{_minutes(prep.time.total)}.{note}")
    return ahead, now


def steps(
    recipe: Recipe,
    resolution: Resolution,
    prepared: dict[str, PreparedIngredient] | None = None,
) -> list[str]:
    """The outline, one sentence per step. Unfilled optional slots are skipped."""
    prepared = PREPARED_INGREDIENTS if prepared is None else prepared
    slots = {s.id: s for s in recipe.slots}
    assigned = {a.slot_id: a.assigned for a in resolution.assignments if a.assigned}
    used = [i for a in resolution.assignments for i in a.assigned]
    ahead, prep = _prep_steps(list(dict.fromkeys(used)), prepared)

    out_edges: dict[str, list[tuple[EdgeType, str]]] = {}
    for edge in recipe.edges:
        if edge.to_slot in assigned:
            out_edges.setdefault(edge.from_slot, []).append(
                (edge.edge_type, slots[edge.to_slot].label.lower())
            )

    main = []
    for slot_id in _order(recipe):
        ids = assigned.get(slot_id)
        if not ids:
            continue
        slot = slots[slot_id]
        sentence = (
            f"{slot.label}: {_verb(slot, ids, prepared)} the {_names(ids)}"
            f"{_minutes(slot.time.total)}"
        )
        joins = [
            EDGE_PHRASE[kind].format(to=to) for kind, to in out_edges.get(slot_id, [])
        ]
        if joins:
            sentence += ", then " + " and ".join(joins)
        main.append(sentence + ".")
    return [*ahead, *prep, *main, "Serve."]
