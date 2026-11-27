"""
garde.timing
=============
How long a resolved recipe takes, using the recipe DAG.

Each slot is a step taking slot.time, plus the time of any prepared forms
assigned to it (roasting the carrots happens before the gravy slot can start).
Edges run from -> to. Cooking edges (combine, fold_in, deglaze, ...) mean "to"
carries on cooking after "from" goes in, so they run in sequence. Assembly edges
(coat, top, finish) only meet at the end: the rice boils while the sauce simmers.
Total time is the longest path.
Make-ahead prepared forms (soaking cashews) are off the critical path.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from functools import cache

from garde.models import EdgeType
from garde.models import PreparedIngredient
from garde.models import Recipe


# Edges where both sides cook independently and are only put together at the end
ASSEMBLY_EDGES: frozenset[EdgeType] = frozenset({"coat", "top", "finish"})


@dataclass
class Timing:
    active: int = 0  # hands-on minutes, summed over every step
    total: int = 0  # minutes from starting to eating (critical path)
    make_ahead: list[str] = field(default_factory=list)  # prep to do earlier


def recipe_timing(
    recipe: Recipe,
    assigned: dict[str, list[str]],
    prepared_db: dict[str, PreparedIngredient],
) -> Timing:
    """assigned: slot id -> ingredient ids placed in it."""
    slots = {s.id: s for s in recipe.slots}
    active = 0
    make_ahead: list[str] = []
    duration: dict[str, int] = {}
    for slot_id, slot in slots.items():
        prep_minutes = 0
        active += slot.time.active
        for ing_id in assigned.get(slot_id, []):
            prep = prepared_db.get(ing_id)
            if prep is None:
                continue
            active += prep.time.active
            if prep.make_ahead:
                make_ahead.append(ing_id)
            else:
                prep_minutes = max(prep_minutes, prep.time.total)
        duration[slot_id] = slot.time.total + prep_minutes

    before: dict[str, list[str]] = {s: [] for s in slots}  # must finish first
    alongside: dict[str, list[str]] = {s: [] for s in slots}  # meet at the end
    for edge in recipe.edges:
        kind = alongside if edge.edge_type in ASSEMBLY_EDGES else before
        kind[edge.to_slot].append(edge.from_slot)

    @cache
    def finish(slot_id: str) -> int:
        start = max((finish(p) for p in before[slot_id]), default=0)
        ready = max((finish(p) for p in alongside[slot_id]), default=0)
        return max(start + duration[slot_id], ready)

    # One cook: hands-on steps can't overlap each other
    total = max(max((finish(s) for s in slots), default=0), active)
    return Timing(active=active, total=total, make_ahead=make_ahead)


def has_cycle(recipe: Recipe) -> bool:
    succ: dict[str, list[str]] = {s.id: [] for s in recipe.slots}
    for edge in recipe.edges:
        succ.setdefault(edge.from_slot, []).append(edge.to_slot)
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(m) for m in succ.get(n, [])):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in succ)
