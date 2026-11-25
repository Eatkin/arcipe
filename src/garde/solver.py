"""
garde.solver
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
              + price      * price points spent (Price level x packs bought)
              + leftovers  * meals of leftovers (breakfast/snack ones count less)

Shopping (optional): one shop on day 1. Anything short is bought in whole packs;
whatever's left in the pack stays in stock for later meals (or goes to waste).
Fresh things only count as buyable for meals within their shelf life.

Leftovers: a meal can be LEFTOVERS (eat the soonest-expiring cooked dish that
suits it) or "recipe*2" (cook enough for two meals, if the dish keeps). Batch
recipes (a loaf) always make a whole batch. Leftovers cost a few minutes of
reheating, no repeat penalty, and a small `leftovers` weight instead.

Search: start from the greedy plan, then simulated annealing over "change one
meal" and "swap two meals" moves. Evaluations are cached by plan.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from typing import Protocol

from garde.loader import INGREDIENTS
from garde.loader import RECIPES
from garde.models import Ingredient
from garde.models import Recipe
from garde.planner import InventoryInput
from garde.planner import Leftover
from garde.planner import Lot
from garde.planner import MealPlan
from garde.planner import MealSlot
from garde.planner import MinutesLimit
from garde.planner import classes_used_recently
from garde.planner import consume
from garde.planner import cook_batches
from garde.planner import cook_servings
from garde.planner import eat_leftovers
from garde.planner import expire
from garde.planner import expire_dishes
from garde.planner import limit_for
from garde.planner import parse_inventory
from garde.planner import plan_week
from garde.planner import stash
from garde.planner import totals
from garde.planner import urgency
from garde.planner import variety_score
from garde.resolver import Resolution
from garde.resolver import resolve
from garde.vibes import Feedback


# Food still in stock that expires within this many days after the plan counts
# (at the `spill` weight) — otherwise the solver would happily leave a fridge of
# spinach that goes off the morning after
SPILL_DAYS = 3

# Having porridge every day is normal; having the same dinner every day isn't
REPEAT_FACTOR = {"breakfast": 0.25, "snack": 0.25}
# ...and an hour making breakfast hurts more than an hour making dinner
TIME_FACTOR = {"breakfast": 3.0, "lunch": 1.5}
# Warming up / plating leftovers
LEFTOVER_MINUTES = 5

# Choice entries: a recipe id, "<recipe id>*2" (cook double), LEFTOVERS, or None
LEFTOVERS = "~leftovers"
DOUBLE = "*2"
Choice = tuple[str | None, ...]  # one entry per (day, meal), day-major


def parse_pick(pick: str) -> tuple[str, int]:
    """'chilli*2' -> ('chilli', 2)."""
    if pick.endswith(DOUBLE):
        return pick.removesuffix(DOUBLE), 2
    return pick, 1


@dataclass(frozen=True)
class Weights:
    waste: float = 1.0  # per 100g that goes off unused during the plan
    spill: float = 0.5  # per 100g left that goes off just after the plan
    unplanned: float = 10.0  # per meal with nothing to cook
    repeat: float = 2.0  # per extra appearance of a recipe
    variety: float = 1.0  # per meal, fraction of its ingredient classes eaten recently
    time: float = 0.2  # per hour of total cooking time
    liked: float = 2.0  # per meal, times its learned recipe score (-1..1)
    price: float = 0.5  # per price point spent when shopping (a pricey pack = 3)
    leftovers: float = 0.5  # per meal of leftovers (breakfast/snack: a quarter)


@dataclass
class Breakdown:
    waste_g: float = 0.0
    spill_g: float = 0.0
    unplanned: int = 0
    repeats: float = 0.0  # weighted: breakfast repeats count for less
    variety: float = 0.0
    minutes: int = 0
    weighted_hours: float = 0.0  # cooking time with breakfast/lunch weighted up
    liked: float = 0.0
    price_points: int = 0  # sum of Price level x packs bought
    leftover_meals: float = 0.0  # weighted like repeats
    objective: float = 0.0

    def describe(self) -> str:
        return (
            f"objective {self.objective:6.2f} | waste {self.waste_g:5.0f}g"
            f" | spill {self.spill_g:5.0f}g | unplanned {self.unplanned}"
            f" | repeats {self.repeats:g} | variety {self.variety:4.2f}"
            f" | {self.minutes} min cooking | liked {self.liked:+.2f}"
            f" | spend {self.price_points} | leftovers {self.leftover_meals:g}"
        )


class Progress(Protocol):
    """Called every PROGRESS_EVERY steps of the search. Raise to stop it."""

    def __call__(
        self, done: int, total: int, weeks_tried: int, best: Breakdown
    ) -> None: ...


PROGRESS_EVERY = 50


@dataclass
class SolverResult:
    plan: MealPlan
    breakdown: Breakdown
    greedy: Breakdown  # the greedy planner's week, scored the same way
    evaluations: int
    # Why each empty meal is empty, keyed (day, meal); see why_unplanned
    reasons: dict[tuple[int, str], str] = field(default_factory=dict)


@dataclass
class _Problem:
    lots: list[Lot]
    days: int
    meals: list[str]
    recipes: dict[str, Recipe]
    ingredient_db: dict[str, Ingredient]
    people: int
    max_minutes: MinutesLimit
    gap: int
    weights: Weights
    feedback: Feedback | None = None
    shopping: bool = False
    leftovers: tuple[Leftover, ...] = ()  # cooked dishes in the fridge at the start
    buyable_cache: dict[int, frozenset[str]] = field(default_factory=dict)
    cache: dict[Choice, tuple[MealPlan, Breakdown]] = field(default_factory=dict)
    start_cache: dict[str, Resolution] | None = None


def _weighted_repeats(planned: list[MealSlot]) -> float:
    seen: set[str] = set()
    repeats = 0.0
    for slot in planned:
        if slot.leftovers_from is not None:
            continue  # eating up a dish isn't cooking it again
        if slot.recipe_id in seen:
            repeats += REPEAT_FACTOR.get(slot.meal, 1.0)
        elif slot.recipe_id:
            seen.add(slot.recipe_id)
    return repeats


def buyable_on(day: int, p: _Problem) -> frozenset[str]:
    """What the day-1 shop could provide for a meal on `day`."""
    if not p.shopping:
        return frozenset()
    if day not in p.buyable_cache:
        p.buyable_cache[day] = frozenset(
            i
            for i, ing in p.ingredient_db.items()
            if not ing.unlimited
            and (ing.shelf_life_days is None or ing.shelf_life_days >= day)
        )
    return p.buyable_cache[day]


def _cook(p: _Problem, recipe_id: str, lots: list[Lot], day: int, times: int = 1):
    """Resolve one meal against current stock (+ the shop); None if it can't be made."""
    recipe = p.recipes[recipe_id]
    res = resolve(
        recipe,
        totals(lots),
        p.ingredient_db,
        servings=cook_servings(recipe, p.people, times),
        urgency=urgency(lots, day),
        rng=random.Random(0),
        affinity=p.feedback.ingredient_affinity(recipe) if p.feedback else None,
        buyable=buyable_on(day, p),
    )
    limit = limit_for(p.max_minutes, day)
    too_long = limit is not None and res.timing.total > limit
    return None if not res.success or too_long else res


def _buy(p: _Problem, consumed: Counter[str], lots: list[Lot]) -> Counter[str]:
    """Buy whole packs of whatever the meal needs beyond stock. Adds them as lots."""
    have = totals(lots)
    packs: Counter[str] = Counter()
    for raw, grams in consumed.items():
        short = grams - have.get(raw, 0.0)
        ing = p.ingredient_db.get(raw)
        if short <= 1e-6 or ing is None or ing.unlimited:
            continue
        n = math.ceil(short / ing.pack_grams - 1e-9)
        packs[raw] = n
        lots.append(Lot(raw, n * ing.pack_grams, ing.shelf_life_days))
    return packs


def simulate(choice: Choice, p: _Problem) -> tuple[MealPlan, Breakdown]:
    """Cook the given week against the stock and score it. Deterministic."""
    if choice in p.cache:
        return p.cache[choice]

    lots = list(p.lots)
    planned: list[MealSlot] = []
    unplanned: list[tuple[int, str]] = []
    wasted: Counter[str] = Counter()
    bought: Counter[str] = Counter()
    dishes: list[Leftover] = list(p.leftovers)
    made: Counter[str] = Counter()
    b = Breakdown()
    picks = iter(choice)

    for day in range(1, p.days + 1):
        lots, gone = expire(lots, day)
        wasted += gone
        dishes, gone = expire_dishes(dishes, day)
        wasted += gone
        for meal in p.meals:
            pick = next(picks)
            if pick == LEFTOVERS:
                dishes, dish = eat_leftovers(dishes, meal, p.people)
                if dish is None:
                    planned.append(MealSlot(day=day, meal=meal))
                    unplanned.append((day, meal))
                    continue
                b.leftover_meals += REPEAT_FACTOR.get(meal, 1.0)
                b.minutes += LEFTOVER_MINUTES
                b.weighted_hours += LEFTOVER_MINUTES / 60 * TIME_FACTOR.get(meal, 1.0)
                if p.feedback:
                    b.liked += p.feedback.recipe_score(dish.recipe_id)
                planned.append(
                    MealSlot(
                        day=day,
                        meal=meal,
                        recipe_id=dish.recipe_id,
                        resolution=dish.resolution,
                        leftovers_from=dish.cooked_day,
                    )
                )
                continue
            recipe_id, times = parse_pick(pick) if pick else (None, 1)
            res = _cook(p, recipe_id, lots, day, times) if recipe_id else None
            if res is None or recipe_id is None:
                planned.append(MealSlot(day=day, meal=meal))
                unplanned.append((day, meal))
                continue
            recipe = p.recipes[recipe_id]
            recent = classes_used_recently(planned, day, p.gap, p.ingredient_db)
            b.variety += 1.0 - variety_score(res, recent, p.ingredient_db)
            b.minutes += res.timing.total
            # A loaf gets baked whenever suits, not at 7am
            factor = 1.0 if recipe.batch else TIME_FACTOR.get(meal, 1.0)
            b.weighted_hours += res.timing.total / 60 * factor
            if p.feedback:
                b.liked += p.feedback.recipe_score(recipe_id)
            planned.append(
                MealSlot(day=day, meal=meal, recipe_id=recipe_id, resolution=res)
            )
            bought += _buy(p, res.consumed, lots)
            lots = consume(lots, res.consumed)
            lots, cooked = cook_batches(lots, res, day, p.ingredient_db)
            made += cooked
            dishes, gone = stash(dishes, recipe, res, p.people, day)
            wasted += gone

    lots, gone = expire(lots, p.days + 1)
    wasted += gone
    dishes, gone = expire_dishes(dishes, p.days + 1)
    wasted += gone

    b.waste_g = sum(wasted.values())
    b.spill_g = sum(
        lot.grams
        for lot in lots
        if lot.expires_day is not None and lot.expires_day <= p.days + SPILL_DAYS
    ) + sum(
        d.portions * d.grams_per_portion
        for d in dishes
        if d.expires_day <= p.days + SPILL_DAYS
    )
    b.unplanned = len(unplanned)
    b.repeats = _weighted_repeats(planned)
    b.price_points = sum(int(p.ingredient_db[i].price) * n for i, n in bought.items())
    w = p.weights
    b.objective = (
        w.waste * b.waste_g / 100
        + w.spill * b.spill_g / 100
        + w.unplanned * b.unplanned
        + w.repeat * b.repeats
        + w.variety * b.variety
        + w.time * b.weighted_hours
        - w.liked * b.liked
        + w.price * b.price_points
        + w.leftovers * b.leftover_meals
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
        bought=bought,
        leftovers=dishes,
        made=made,
    )
    p.cache[choice] = (plan, b)
    return plan, b


def _makeable(p: _Problem) -> dict[str, Resolution]:
    """Every recipe makeable from the starting stock (and the shop), time aside."""
    if p.start_cache is None:
        start = totals(p.lots)
        p.start_cache = {}
        for rid, r in p.recipes.items():
            res = resolve(
                r,
                start,
                p.ingredient_db,
                servings=cook_servings(r, p.people),
                rng=random.Random(0),
                buyable=buyable_on(1, p),
            )
            if res.success:
                p.start_cache[rid] = res
    return p.start_cache


def why_unplanned(p: _Problem, day: int, meal: str) -> str:
    """A plain-English reason a meal ended up with nothing to cook."""
    shop_hint = "" if p.shopping else " Turn on shopping to buy what's missing."
    if not any(meal in r.meal_type for r in p.recipes.values()):
        return f"There are no {meal} recipes yet."
    times = [
        res.timing.total
        for rid, res in _makeable(p).items()
        if meal in p.recipes[rid].meal_type
    ]
    if not times:
        if p.shopping:
            return f"No {meal} can be made from the pantry, even with shopping."
        return f"No {meal} can be made from what's in the pantry.{shop_hint}"
    limit = limit_for(p.max_minutes, day)
    if limit is not None and min(times) > limit:
        return (
            f"The quickest {meal} you could make takes {min(times)} min,"
            f" over the {limit} min limit for this day."
        )
    return (
        f"Nothing left by then makes a {meal}: earlier meals used what it"
        f" needed, or it went off.{shop_hint}"
    )


def quickest_minutes(
    recipes: dict[str, Recipe] | None = None,
    ingredient_db: dict[str, Ingredient] | None = None,
) -> dict[str, int]:
    """Quickest recipe for each meal type with everything in stock, in minutes."""
    db = ingredient_db or INGREDIENTS
    plenty = dict.fromkeys(db, 10000.0)
    out: dict[str, int] = {}
    for r in (recipes or RECIPES).values():
        res = resolve(r, plenty, db, servings=2, rng=random.Random(0))
        if not res.success:
            continue
        for meal in r.meal_type:
            out[meal] = min(out.get(meal, res.timing.total), res.timing.total)
    return out


def _options(p: _Problem) -> list[list[str | None]]:
    """
    Recipes worth trying at each position: right meal type, and makeable from the
    starting stock at all (stock only shrinks, so anything else never will be).
    Dishes that keep can also be cooked double, and any meal some dish that keeps
    would suit can be leftovers.
    """
    makeable = set(_makeable(p))
    keeps = [p.recipes[rid] for rid in makeable if p.recipes[rid].keeps_days > 0]
    per_meal: dict[str, list[str | None]] = {}
    for meal in p.meals:
        here = sorted(rid for rid in makeable if meal in p.recipes[rid].meal_type)
        doubles = [
            rid + DOUBLE
            for rid in here
            if p.recipes[rid].keeps_days > 0 and not p.recipes[rid].batch
        ]
        leftovers = (
            [LEFTOVERS]
            if any(meal in r.meal_type for r in keeps)
            or any(meal in d.meal_types for d in p.leftovers)
            else []
        )
        per_meal[meal] = [*here, *doubles, *leftovers, None]
    return [per_meal[meal] for _ in range(p.days) for meal in p.meals]


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
    tick: Callable[[Choice], None] | None = None,
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
        if tick is not None:
            tick(best)
    return best


def optimise_week(
    recipes: dict[str, Recipe] | None = None,
    inventory: InventoryInput | None = None,
    days: int = 7,
    meals_per_day: list[str] | None = None,
    people: int = 2,
    max_minutes: MinutesLimit = None,
    max_same_class_gap: int = 2,
    weights: Weights | None = None,
    iterations: int = 2000,
    restarts: int = 2,
    ingredient_db: dict[str, Ingredient] | None = None,
    rng: random.Random | None = None,
    feedback: Feedback | None = None,
    shopping: bool = False,
    progress: Progress | None = None,
    leftovers: Sequence[Leftover] = (),
) -> SolverResult:
    """
    Plan the week to minimise the weighted objective (see module docstring).
    Same arguments as plan_week, plus weights and search effort.
    Never does worse than the greedy planner: the search starts from its plan.
    shopping=True lets the plan buy things (see module docstring); the bill is
    in result.plan.bought and result.plan.shopping_list().
    max_minutes can be one limit or a list with one per day.
    progress, if given, hears how the search is going (see Progress).
    leftovers are cooked dishes already in the fridge (garde.pantry.to_leftovers).
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
        shopping=shopping,
        leftovers=tuple(leftovers),
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
        leftovers=leftovers,
    )
    greedy: Choice = tuple(
        LEFTOVERS if s.leftovers_from is not None else s.recipe_id
        for s in greedy_plan.slots
    )
    greedy = tuple(
        c if c in opts else None for c, opts in zip(greedy, options, strict=True)
    )

    best = greedy
    runs = max(restarts, 1)
    total = iterations * runs
    done = 0

    def tick(run_best: Choice) -> None:
        nonlocal done
        done += 1
        if progress is not None and (done % PROGRESS_EVERY == 0 or done == total):
            overall = min((best, run_best), key=lambda c: simulate(c, p)[1].objective)
            progress(done, total, len(p.cache), simulate(overall, p)[1])

    for _ in range(runs):
        found = _anneal(greedy, options, p, iterations, rng, tick)
        if simulate(found, p)[1].objective < simulate(best, p)[1].objective:
            best = found

    plan, breakdown = simulate(best, p)
    return SolverResult(
        plan=plan,
        breakdown=breakdown,
        greedy=simulate(greedy, p)[1],
        evaluations=len(p.cache),
        reasons={(d, m): why_unplanned(p, d, m) for d, m in plan.unplanned},
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
