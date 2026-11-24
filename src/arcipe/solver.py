"""
arcipe.solver
=============
Whole-week optimiser. The greedy planner picks the best meal for today without
looking ahead, so it can't see that the mushrooms go off on day 4. This searches
over the whole week instead.

A candidate plan is just a recipe id (or None) for each (day, meal). It is scored
by simulating the week with the real resolver and expiry logic, so the solver
never disagrees with what the planner would actually cook:

    objective = waste      * 100g of food that went off unused
              + spill      * 100g left that goes off within SPILL_DAYS of the end
              + unplanned  * meals that can't be made
              + repeat     * extra repeats of a recipe
              + variety    * ingredient-class overlap with recent meals (0..1 each)
              + time       * hours of cooking
              - liked      * how much you've liked each recipe cooked (-1..1)

Search: start from the greedy plan, then simulated annealing over "change one
meal" and "swap two meals" moves. Evaluations are cached by plan.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields

from arcipe.loader import INGREDIENTS
from arcipe.loader import RECIPES
from arcipe.models import Ingredient
from arcipe.models import Recipe
from arcipe.planner import InventoryInput
from arcipe.planner import Lot
from arcipe.planner import MealPlan
from arcipe.planner import MealSlot
from arcipe.planner import classes_used_recently
from arcipe.planner import consume
from arcipe.planner import expire
from arcipe.planner import parse_inventory
from arcipe.planner import plan_week
from arcipe.planner import totals
from arcipe.planner import urgency
from arcipe.planner import variety_score
from arcipe.resolver import resolve
from arcipe.vibes import Feedback


# Food still in stock that expires within this many days after the plan counts
# (at the `spill` weight) — otherwise the solver would happily leave a fridge of
# spinach that goes off the morning after
SPILL_DAYS = 3

Choice = tuple[str | None, ...]  # recipe id per (day, meal), day-major


@dataclass(frozen=True)
class Weights:
    waste: float = 1.0  # per 100g that goes off unused during the plan
    spill: float = 0.5  # per 100g left that goes off just after the plan
    unplanned: float = 10.0  # per meal with nothing to cook
    repeat: float = 2.0  # per extra appearance of a recipe
    variety: float = 1.0  # per meal, fraction of its ingredient classes eaten recently
    time: float = 0.2  # per hour of total cooking time
    liked: float = 2.0  # per meal, times its learned recipe score (-1..1)


@dataclass
class Breakdown:
    waste_g: float = 0.0
    spill_g: float = 0.0
    unplanned: int = 0
    repeats: int = 0
    variety: float = 0.0
    minutes: int = 0
    liked: float = 0.0
    objective: float = 0.0

    def describe(self) -> str:
        return (
            f"objective {self.objective:6.2f} | waste {self.waste_g:5.0f}g"
            f" | spill {self.spill_g:5.0f}g | unplanned {self.unplanned}"
            f" | repeats {self.repeats} | variety {self.variety:4.2f}"
            f" | {self.minutes} min cooking | liked {self.liked:+.2f}"
        )


@dataclass
class SolverResult:
    plan: MealPlan
    breakdown: Breakdown
    greedy: Breakdown  # the greedy planner's week, scored the same way
    evaluations: int


@dataclass
class _Problem:
    lots: list[Lot]
    days: int
    meals: list[str]
    recipes: dict[str, Recipe]
    ingredient_db: dict[str, Ingredient]
    people: int
    max_minutes: int | None
    gap: int
    weights: Weights
    feedback: Feedback | None = None
    cache: dict[Choice, tuple[MealPlan, Breakdown]] = field(default_factory=dict)


def simulate(choice: Choice, p: _Problem) -> tuple[MealPlan, Breakdown]:
    """Cook the given week against the stock and score it. Deterministic."""
    if choice in p.cache:
        return p.cache[choice]

    lots = list(p.lots)
    planned: list[MealSlot] = []
    unplanned: list[tuple[int, str]] = []
    wasted: Counter[str] = Counter()
    b = Breakdown()
    picks = iter(choice)

    for day in range(1, p.days + 1):
        lots, gone = expire(lots, day)
        wasted += gone
        for meal in p.meals:
            recipe_id = next(picks)
            res = None
            if recipe_id is not None:
                res = resolve(
                    p.recipes[recipe_id],
                    totals(lots),
                    p.ingredient_db,
                    servings=p.people,
                    urgency=urgency(lots, day),
                    rng=random.Random(0),
                    affinity=(
                        p.feedback.ingredient_affinity(p.recipes[recipe_id])
                        if p.feedback
                        else None
                    ),
                )
                too_long = (
                    p.max_minutes is not None and res.timing.total > p.max_minutes
                )
                if not res.success or too_long:
                    res = None
            if res is None:
                planned.append(MealSlot(day=day, meal=meal))
                unplanned.append((day, meal))
                continue
            recent = classes_used_recently(planned, day, p.gap, p.ingredient_db)
            b.variety += 1.0 - variety_score(res, recent, p.ingredient_db)
            b.minutes += res.timing.total
            if p.feedback and recipe_id:
                b.liked += p.feedback.recipe_score(recipe_id)
            planned.append(
                MealSlot(day=day, meal=meal, recipe_id=recipe_id, resolution=res)
            )
            lots = consume(lots, res.consumed)

    lots, gone = expire(lots, p.days + 1)
    wasted += gone

    cooked = [s.recipe_id for s in planned if s.recipe_id]
    b.waste_g = sum(wasted.values())
    b.spill_g = sum(
        lot.grams
        for lot in lots
        if lot.expires_day is not None and lot.expires_day <= p.days + SPILL_DAYS
    )
    b.unplanned = len(unplanned)
    b.repeats = len(cooked) - len(set(cooked))
    w = p.weights
    b.objective = (
        w.waste * b.waste_g / 100
        + w.spill * b.spill_g / 100
        + w.unplanned * b.unplanned
        + w.repeat * b.repeats
        + w.variety * b.variety
        + w.time * b.minutes / 60
        - w.liked * b.liked
    )

    plan = MealPlan(
        days=p.days,
        meals_per_day=p.meals,
        slots=planned,
        inventory_start=totals(p.lots),
        inventory_remaining=totals(lots),
        unplanned=unplanned,
        wasted=wasted,
        remaining_lots=lots,
    )
    p.cache[choice] = (plan, b)
    return plan, b


def _options(p: _Problem) -> list[list[str | None]]:
    """
    Recipes worth trying at each position: right meal type, and makeable from the
    starting stock at all (stock only shrinks, so anything else never will be).
    """
    start = totals(p.lots)
    makeable = {
        rid
        for rid, r in p.recipes.items()
        if resolve(
            r, start, p.ingredient_db, servings=p.people, rng=random.Random(0)
        ).success
    }
    per_meal = {
        meal: sorted(rid for rid in makeable if meal in p.recipes[rid].meal_type)
        for meal in p.meals
    }
    return [[*per_meal[meal], None] for _ in range(p.days) for meal in p.meals]


def _neighbour(
    choice: Choice, options: list[list[str | None]], rng: random.Random
) -> Choice:
    out = list(choice)
    if rng.random() < 0.5 and len(out) > 1:
        i, j = rng.sample(range(len(out)), 2)
        if out[i] in options[j] and out[j] in options[i]:
            out[i], out[j] = out[j], out[i]
            return tuple(out)
    i = rng.randrange(len(out))
    out[i] = rng.choice(options[i])
    return tuple(out)


def _anneal(
    start: Choice,
    options: list[list[str | None]],
    p: _Problem,
    iterations: int,
    rng: random.Random,
) -> Choice:
    current = best = start
    current_score = best_score = simulate(start, p)[1].objective
    t_start, t_end = 2.0, 0.02
    for k in range(iterations):
        temp = t_start * (t_end / t_start) ** (k / max(iterations - 1, 1))
        candidate = _neighbour(current, options, rng)
        score = simulate(candidate, p)[1].objective
        delta = score - current_score
        if delta <= 0 or rng.random() < math.exp(-delta / temp):
            current, current_score = candidate, score
            if score < best_score:
                best, best_score = candidate, score
    return best


def optimise_week(
    recipes: dict[str, Recipe] | None = None,
    inventory: InventoryInput | None = None,
    days: int = 7,
    meals_per_day: list[str] | None = None,
    people: int = 2,
    max_minutes: int | None = None,
    max_same_class_gap: int = 2,
    weights: Weights | None = None,
    iterations: int = 2000,
    restarts: int = 2,
    ingredient_db: dict[str, Ingredient] | None = None,
    rng: random.Random | None = None,
    feedback: Feedback | None = None,
) -> SolverResult:
    """
    Plan the week to minimise the weighted objective (see module docstring).
    Same arguments as plan_week, plus weights and search effort.
    Never does worse than the greedy planner: the search starts from its plan.
    """
    rng = rng or random.Random()
    db = ingredient_db or INGREDIENTS
    meals = meals_per_day or ["dinner"]
    p = _Problem(
        lots=parse_inventory(inventory or {}, db),
        days=days,
        meals=meals,
        recipes=recipes or RECIPES,
        ingredient_db=db,
        people=people,
        max_minutes=max_minutes,
        gap=max_same_class_gap,
        weights=weights or Weights(),
        feedback=feedback,
    )
    options = _options(p)

    greedy_plan = plan_week(
        p.recipes,
        inventory,
        days=days,
        meals_per_day=meals,
        people=people,
        max_minutes=max_minutes,
        max_same_class_gap=max_same_class_gap,
        ingredient_db=db,
        randomise=False,
        rng=random.Random(rng.random()),
        feedback=feedback,
    )
    greedy: Choice = tuple(s.recipe_id for s in greedy_plan.slots)
    greedy = tuple(
        c if c in opts else None for c, opts in zip(greedy, options, strict=True)
    )

    best = greedy
    for _ in range(max(restarts, 1)):
        found = _anneal(greedy, options, p, iterations, rng)
        if simulate(found, p)[1].objective < simulate(best, p)[1].objective:
            best = found

    plan, breakdown = simulate(best, p)
    return SolverResult(
        plan=plan,
        breakdown=breakdown,
        greedy=simulate(greedy, p)[1],
        evaluations=len(p.cache),
    )


def weights_from(overrides: dict[str, float]) -> Weights:
    """Weights with some fields overridden, e.g. from a dashboard's sliders."""
    known = {f.name for f in fields(Weights)}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"unknown weights: {sorted(unknown)}")
    return Weights(**overrides)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    demo_inventory: InventoryInput = {
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
        "cashews": "200g",
        "sun_dried_tomatoes": 1,
        "frozen_spinach": "900g",
        "vegan_parmesan": "150g",
        "green_onion": 4,
        "tofu": 1,
        "apple": 2,
        "butter": "250g",
    }
    t = time.perf_counter()
    result = optimise_week(inventory=demo_inventory, rng=random.Random(0))
    elapsed = time.perf_counter() - t
    print(result.plan.summary())
    print()
    print(f"greedy    {result.greedy.describe()}")
    print(f"optimised {result.breakdown.describe()}")
    print(f"{result.evaluations} plans simulated in {elapsed:.1f}s")
