"""
Recipe Graph resolution
=======================
Resolves abstract recipes into concrete ingredient assignments given an inventory.

Quantities:
    Stock is grams *as stocked* per raw ingredient (dried lentils, whole carrots).
    Slot amounts are grams *as it lands in the dish*; Ingredient.stock_ratio and
    PreparedIngredient.base_ratio convert one to the other. Passing a plain list of
    ids as inventory means "plenty of each".

Flow:
    available_pool(inventory)   -> raw ids + unlimited staples + makeable prepared forms
    candidates(slot, pool)      -> ingredients matching the slot's roles/method/colour
    score(ingredient, slot)     -> float
    resolve(recipe, inventory)  -> Resolution

Reuse rule (recipe.no_ingredient_reuse): every pick "claims" the raw ingredients it is
made from (its prepared-form bases, or itself). A later pick that would claim one of the
same raw ingredients is rejected, so carrot and roasted_carrot can't both appear.
Ingredients flagged `staple` are never claimed and can be used freely.

Made ingredients (Ingredient.made_from): with none of it in stock, cooked chickpeas
can still come from dried chickpeas. Drawing takes what's stocked first, then makes
the rest from the source; Resolution.made says how much was made so the planner can
cook a whole batch and keep the rest.

Scraps: after the dish is filled, a perishable ingredient that would be left with
less than Ingredient.crumb grams goes into the dish too.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from itertools import groupby

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS
from garde.models import COLOUR_MAX
from garde.models import CUISINE_FAMILY
from garde.models import ComponentSlot
from garde.models import Ingredient
from garde.models import PreparedIngredient
from garde.models import Recipe
from garde.models import Season
from garde.models import colour_distance
from garde.timing import Timing
from garde.timing import recipe_timing
from garde.utils import get_season
from garde.vibes import Feedback


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------


@dataclass
class SlotAssignment:
    slot_id: str
    slot_label: str
    assigned: list[str]  # ingredient ids, up to slot.max_ingredients
    candidates: list[str]  # all valid candidates before selection
    filled: bool  # False if required slot could not be filled
    grams: dict[str, float] = field(
        default_factory=dict
    )  # in-dish grams per assigned id


@dataclass
class Resolution:
    recipe_id: str
    recipe_name: str
    assignments: list[SlotAssignment]
    used: set[str]  # ingredient ids (raw or prepared) assigned across all slots
    unfillable: list[str]  # slot ids that could not be filled (required only)
    missing_required: list[str] = field(default_factory=list)  # not available at all
    unplaceable_required: list[str] = field(default_factory=list)  # no slot accepts it
    # Stocked grams of each raw ingredient used, prepared forms expanded to their bases
    consumed: Counter[str] = field(default_factory=Counter)
    servings: int = 0
    timing: Timing = field(default_factory=Timing)
    # Grams of made ingredients (made_from) produced from their source for this dish
    made: Counter[str] = field(default_factory=Counter)

    @property
    def success(self) -> bool:
        return not (
            self.unfillable or self.missing_required or self.unplaceable_required
        )

    def summary(self) -> str:
        lines = [
            f"[{self.recipe_name}] {'✓' if self.success else '✗'}"
            f"  {self.timing.total} min ({self.timing.active} hands-on)"
        ]
        if self.timing.make_ahead:
            lines.append(f"  Make ahead: {', '.join(self.timing.make_ahead)}")
        for a in self.assignments:
            status = "✓" if a.filled else "✗ UNFILLABLE"
            assigned = (
                ", ".join(_fmt_pick(i, a.grams.get(i)) for i in a.assigned)
                if a.assigned
                else "—"
            )
            lines.append(f"  {a.slot_label:<22} {status}  →  {assigned}")
        if self.missing_required:
            lines.append(f"\n  Missing required: {', '.join(self.missing_required)}")
        if self.unplaceable_required:
            lines.append(
                f"\n  Required but no slot accepts: {', '.join(self.unplaceable_required)}"
            )
        if self.unfillable:
            lines.append(f"\n  Unfillable slots: {', '.join(self.unfillable)}")
        return "\n".join(lines)


def _fmt_pick(ing_id: str, grams: float | None) -> str:
    return f"{ing_id} {grams:.0f}g" if grams else ing_id


# ---------------------------------------------------------------------------
# Inventory expansion
# ---------------------------------------------------------------------------


INF = float("inf")

Inventory = list[str] | dict[str, float]


def normalise_stock(
    inventory: Inventory, ingredient_db: dict[str, Ingredient]
) -> dict[str, float]:
    """
    Stocked grams per raw ingredient. A list means plenty of each. Unlimited
    staples are infinite unless the inventory tracks them explicitly.
    """
    if isinstance(inventory, dict):
        stock = {i: float(g) for i, g in inventory.items() if i in ingredient_db}
    else:
        stock = {i: INF for i in inventory if i in ingredient_db}
    for ing_id, ing in ingredient_db.items():
        if ing.unlimited and ing_id not in stock:
            stock[ing_id] = INF
    return stock


def have(
    raw: str, stock: dict[str, float], ingredient_db: dict[str, Ingredient]
) -> float:
    """Stocked grams of `raw` on hand, counting what could be made from its source."""
    grams = stock.get(raw, 0.0)
    ing = ingredient_db.get(raw)
    if ing is not None and ing.made_from is not None:
        grams += stock.get(ing.made_from.ingredient, 0.0) / ing.made_from.ratio
    return grams


def stocked_per_gram(
    ing_id: str,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> dict[str, float]:
    """Stocked grams of each raw ingredient needed per in-dish gram of `ing_id`."""
    prep = prepared_db.get(ing_id)
    if prep is None:
        return {ing_id: ingredient_db[ing_id].stock_ratio}
    return {
        b: prep.base_ratio.get(b, 1.0) * ingredient_db[b].stock_ratio
        for b in prep.base_ingredients
    }


def makeable_grams(
    ing_id: str,
    stock: dict[str, float],
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> float:
    """How many in-dish grams of `ing_id` the stock can provide."""
    per_gram = stocked_per_gram(ing_id, ingredient_db, prepared_db)
    return min(
        (have(raw, stock, ingredient_db) / ratio if ratio > 0 else INF)
        for raw, ratio in per_gram.items()
    )


def available_pool(
    inventory: Inventory,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> set[str]:
    """
    Everything that could go into a recipe: raw ingredients in stock, unlimited
    staples, and any prepared form whose base ingredients are all in stock.
    """
    stock = normalise_stock(inventory, ingredient_db)
    raw = {i for i, g in stock.items() if g > 0} | {
        i
        for i, ing in ingredient_db.items()
        if ing.made_from is not None and stock.get(ing.made_from.ingredient, 0.0) > 0
    }
    makeable = {
        p.id for p in prepared_db.values() if all(b in raw for b in p.base_ingredients)
    }
    return raw | makeable


# Prepared forms as Ingredients, built once per (prep, bases) and reused, so that
# slot-match results can be cached against them
_SYNTHETIC: dict[tuple[int, ...], tuple[Ingredient, PreparedIngredient, list]] = {}
# (slot, ingredient, prepared_db, min_coverage) -> does it fit. Values hold the
# objects too, so a cached id is never reused by a different object
_MATCHES: dict[tuple[int, int, int, float], tuple[bool, object, object, object]] = {}


def expand_inventory(
    inventory: Inventory,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> dict[str, Ingredient]:
    """
    Return ingredient_db plus a synthetic Ingredient for every prepared form whose
    bases are all available, so prepared forms can be matched and scored like raw ones.
    """
    expanded = dict(ingredient_db)
    pool = available_pool(inventory, ingredient_db, prepared_db)
    for prep in prepared_db.values():
        if prep.id not in pool:
            continue
        bases = [ingredient_db[b] for b in prep.base_ingredients]
        key = (id(prep), *map(id, bases))
        if key in _SYNTHETIC:
            expanded[prep.id] = _SYNTHETIC[key][0]
            continue
        base = bases[0]
        lives = [b.shelf_life_days for b in bases if b.shelf_life_days is not None]
        expanded[prep.id] = Ingredient(
            id=prep.id,
            name=f"{prep.id.replace('_', ' ')} (prepared)",
            classes=base.classes,
            roles=prep.unlocked_roles,
            flavour=prep.flavour or base.flavour,
            colour=prep.colour or base.colour,
            season=base.season,
            price=max(b.price for b in bases),
            nutrition=base.nutrition,
            shelf_life_days=min(lives) if lives else None,
            unlimited=all(b.unlimited for b in bases),
            cuisine_context=prep.cuisine_context,
            notes=prep.notes,
        )
        # keep prep and bases alive alongside, so their ids can't be reused
        _SYNTHETIC[key] = (expanded[prep.id], prep, bases)
    return expanded


def consumes(ing_id: str, prepared_db: dict[str, PreparedIngredient]) -> list[str]:
    """Raw ingredient ids used up by assigning `ing_id` (its bases if prepared)."""
    prep = prepared_db.get(ing_id)
    return list(prep.base_ingredients) if prep else [ing_id]


def reuse_keys(
    ing_id: str,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> set[str]:
    """Raw ingredients an assignment claims under no_ingredient_reuse (staples exempt)."""
    return {
        raw
        for raw in consumes(ing_id, prepared_db)
        if not (raw in ingredient_db and ingredient_db[raw].staple)
    }


# ---------------------------------------------------------------------------
# Candidate matching
# ---------------------------------------------------------------------------


def matches_slot(
    ing: Ingredient,
    slot: ComponentSlot,
    prepared_db: dict[str, PreparedIngredient],
    min_coverage: float = 1.0,
) -> bool:
    """Does this ingredient satisfy the slot's colour, prep method and role requirements?"""
    if slot.required_colour:
        dist = colour_distance(ing.colour, slot.required_colour)
        if dist / COLOUR_MAX > slot.colour_tolerance:
            return False
    if slot.required_classes and not set(slot.required_classes) & set(ing.classes):
        return False
    if set(slot.excluded_classes) & set(ing.classes):
        return False
    # A prepared form has a fixed method (roasted carrot is roasted); raw ingredients
    # can be cooked however the slot needs.
    prep = prepared_db.get(ing.id)
    if prep and slot.required_method and prep.method not in slot.required_method:
        return False
    required = set(slot.required_roles)
    covered = required & set(ing.roles)
    coverage = len(covered) / len(required) if required else 1.0
    return coverage >= min_coverage


_UNIVERSE: dict[
    tuple[int, int, int], tuple[tuple[str, ...], object, object, object]
] = {}


def slot_universe(
    slot: ComponentSlot,
    ingredient_db: dict[str, Ingredient],
    prepared_db: dict[str, PreparedIngredient],
) -> tuple[str, ...]:
    """
    Every ingredient or prepared form that could ever fill this slot, ignoring
    stock. Cached: matching only depends on the slot and the ingredient data.
    """
    key = (id(slot), id(ingredient_db), id(prepared_db))
    hit = _UNIVERSE.get(key)
    if hit is None:
        everything = expand_inventory(list(ingredient_db), ingredient_db, prepared_db)
        ids = tuple(
            sorted(
                i
                for i, ing in everything.items()
                if matches_slot(ing, slot, prepared_db)
            )
        )
        hit = (ids, slot, ingredient_db, prepared_db)
        _UNIVERSE[key] = hit
    return hit[0]


def candidates(
    slot: ComponentSlot,
    inventory: list[str],
    ingredient_db: dict[str, Ingredient] | None = None,
    min_coverage: float = 1.0,
    prepared_db: dict[str, PreparedIngredient] | None = None,
) -> list[Ingredient]:
    """
    Return all ingredients from inventory that satisfy the slot.
    min_coverage=1.0 enforces full role coverage (default).
    Lower values allow partial matches — useful for 'almost there' suggestions.
    """
    db = ingredient_db or INGREDIENTS
    preps = PREPARED_INGREDIENTS if prepared_db is None else prepared_db
    result = []
    for ing_id in inventory:
        ing = db.get(ing_id)
        if ing is None:
            continue
        key = (id(slot), id(ing), id(preps), min_coverage)
        hit = _MATCHES.get(key)
        if hit is None:
            hit = (matches_slot(ing, slot, preps, min_coverage), slot, ing, preps)
            _MATCHES[key] = hit
        if hit[0]:
            result.append(ing)
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

CURRENT_SEASON: Season = get_season(date.today())

# Cheaper ingredients score higher: lean on the cheap staples
PRICE_SCORE: dict[int, float] = {0: 1.0, 1: 1.0, 2: 0.75, 3: 0.45, 4: 0.2}

# Something you'd have to buy: always worse than what's already in, and pricier
# things more so. Only applies when resolve() is given `buyable`.
BUY_PENALTY = 1.0
BUY_PENALTY_PER_PRICE = 0.3


def buy_penalty(ingredient: Ingredient) -> float:
    return BUY_PENALTY + BUY_PENALTY_PER_PRICE * int(ingredient.price)


def _cuisine_fit(ingredient: Ingredient, recipe: Recipe) -> float:
    """Exact cuisine match +0.4, same family +0.1, different family -0.8."""
    if not (recipe.cuisine_context and ingredient.cuisine_context):
        return 0.0
    if set(recipe.cuisine_context) & set(ingredient.cuisine_context):
        return 0.4
    recipe_families = {CUISINE_FAMILY[c] for c in recipe.cuisine_context}
    ing_families = {CUISINE_FAMILY[c] for c in ingredient.cuisine_context}
    return 0.1 if recipe_families & ing_families else -0.8


# How hard learned taste pushes: a firm like/dislike (~±0.6) is worth about a
# cuisine match
VIBES_SCALE = 1.0


# More than a preferred class (+0.5) or the most urgent use-by (+0.8) can make up
DISCOURAGED_PENALTY = 1.0


def discouraged(ingredient: Ingredient, slot: ComponentSlot) -> bool:
    return bool(set(slot.discouraged_classes) & set(ingredient.classes))


def score(
    ingredient: Ingredient,
    slot: ComponentSlot,
    recipe: Recipe,
    urgency: dict[str, float] | None = None,
    bias: dict[str, float] | None = None,
) -> float:
    """
    Score an ingredient against a slot. Higher is better.

    Components:
        role_coverage     — fraction of slot's required_roles covered
                            (always 1.0 after candidate filtering, but useful if we relax later)
        preferred_class   — bonus if ingredient is in slot.preferred_classes
        discouraged_class — penalty if it's in slot.discouraged_classes
        preferred_role    — bonus if ingredient also covers a slot.preferred_roles role
        season            — bonus if ingredient is in season or available all year
        price             — cheaper ingredients score higher (lean on cheap staples)
        role_depth        — ingredients with more roles are more versatile, slight bonus
        urgency           — use things about to go off first. urgency[id] is 0..1
                            (1 = expires today); without it, a flat perishable bonus
        preferred         — recipe.preferred_ingredients bonus
        cuisine_context   — bonus for exact cuisine match, small bonus for same family,
                            heavy penalty for a different family
        bias              — additive nudge per id: learned taste (garde.vibes) and
                            the penalty for having to buy it
    """
    s = 0.0

    required = set(slot.required_roles)
    covered = required & set(ingredient.roles)
    s += len(covered) / len(required) if required else 1.0

    if any(c in slot.preferred_classes for c in ingredient.classes):
        s += 0.5

    if discouraged(ingredient, slot):
        s -= DISCOURAGED_PENALTY

    if any(r in slot.preferred_roles for r in ingredient.roles):
        s += 0.4

    if "all" in ingredient.season or CURRENT_SEASON in ingredient.season:
        s += 0.3
        if "forage" in ingredient.season:
            s += 0.1  # foraged and in season: free, reward it

    s += PRICE_SCORE[int(ingredient.price)]

    s += min(len(ingredient.roles) / 10, 0.2)

    if urgency is not None:
        s += 0.8 * urgency.get(ingredient.id, 0.0)
    elif ingredient.perishable:
        s += 0.3

    if ingredient.id in recipe.preferred_ingredients:
        s += 0.5

    s += _cuisine_fit(ingredient, recipe)

    if bias:
        s += bias.get(ingredient.id, 0.0)

    return s


def rank(
    viable: list[Ingredient],
    slot: ComponentSlot,
    recipe: Recipe,
    rng: random.Random | None = None,
    urgency: dict[str, float] | None = None,
    bias: dict[str, float] | None = None,
) -> list[Ingredient]:
    """Sort by score descending, shuffling within equal-score groups for variety."""
    rng = rng or random.Random()
    scored = sorted(
        ((score(ing, slot, recipe, urgency, bias), ing.id, ing) for ing in viable),
        key=lambda t: (-t[0], t[1]),
    )
    ranked: list[Ingredient] = []
    for _, group in groupby(scored, key=lambda t: t[0]):
        group_list = [ing for _, _, ing in group]
        rng.shuffle(group_list)
        ranked.extend(group_list)
    return ranked


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


# A slot counts as filled at this fraction of its amount — cooking is forgiving
MIN_SLOT_COVERAGE = 0.75


def slot_grams(slot: ComponentSlot, servings: int) -> float | None:
    """In-dish grams the slot needs, or None for trace/untracked slots."""
    if slot.amount is None or slot.amount == "trace":
        return None
    return slot.amount.grams(servings)


@dataclass
class _Assigner:
    """Tracks slot picks, claimed raw ingredients and the grams left in stock."""

    recipe: Recipe
    ingredient_db: dict[str, Ingredient]
    prepared_db: dict[str, PreparedIngredient]
    stock: dict[str, float]
    chosen: dict[str, dict[str, float]] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    consumed: Counter[str] = field(default_factory=Counter)
    made: Counter[str] = field(default_factory=Counter)
    to_buy: set[str] = field(default_factory=set)  # raw ids not in stock, buyable

    def needs_buying(self, ing_id: str) -> bool:
        return any(raw in self.to_buy for raw in consumes(ing_id, self.prepared_db))

    def keys(self, ing_id: str) -> set[str]:
        if not self.recipe.no_ingredient_reuse:
            return set()
        return reuse_keys(ing_id, self.ingredient_db, self.prepared_db)

    def makeable(self, ing_id: str, servings: int) -> float:
        grams = makeable_grams(ing_id, self.stock, self.ingredient_db, self.prepared_db)
        ing = self.ingredient_db.get(ing_id)
        if ing is not None and ing.max_per_serving is not None:
            grams = min(grams, ing.max_per_serving * servings)
        return grams

    def draw(self, ing_id: str, grams: float) -> None:
        for raw, ratio in stocked_per_gram(
            ing_id, self.ingredient_db, self.prepared_db
        ).items():
            self.take(raw, grams * ratio)

    def take(self, raw: str, grams: float) -> None:
        """Stocked grams of `raw`: from stock first, then made from its source."""
        on_hand = max(self.stock.get(raw, 0.0), 0.0)
        direct = min(grams, on_hand)
        if direct > 0:
            self.stock[raw] = on_hand - direct
            self.consumed[raw] += direct
        short = grams - direct
        made_from = self.ingredient_db[raw].made_from
        if short <= 1e-9 or made_from is None:
            return
        src = made_from.ingredient
        self.stock[src] = self.stock.get(src, 0.0) - short * made_from.ratio
        self.consumed[src] += short * made_from.ratio
        self.made[raw] += short

    def sweep(
        self, start: dict[str, float], grams: dict[str, dict[str, float]]
    ) -> None:
        """Use up scraps: perishables that would be left with less than a crumb."""
        for raw in list(self.consumed):
            ing = self.ingredient_db.get(raw)
            left = self.stock.get(raw, 0.0)
            if ing is None or start.get(raw, 0.0) == INF or not 0 < left < ing.crumb:
                continue
            if not (ing.perishable or (ing.made_from and ing.made_from.keeps_days)):
                continue
            self.stock[raw] = 0.0
            self.consumed[raw] += left
            for slot_grams in grams.values():  # show it in the dish where it went
                if raw in slot_grams:
                    slot_grams[raw] += left / ing.stock_ratio
                    break

    def fill(
        self,
        slot: ComponentSlot,
        ordered: list[Ingredient],
        need: float | None,
        reserved: set[str],
        servings: int,
    ) -> bool:
        """
        Take ingredients in order until the slot's amount is covered (or, for trace
        slots, until max_ingredients). Rolls back if coverage stays too low.
        """
        snapshot = (
            dict(self.stock),
            set(self.claimed),
            Counter(self.consumed),
            Counter(self.made),
        )
        taken: dict[str, float] = {}
        got = 0.0
        share = (
            need / slot.max_ingredients if need is not None and slot.spread else None
        )
        for ing in ordered:
            if len(taken) >= slot.max_ingredients or (need is not None and got >= need):
                break
            if ing.id in taken or self._skip(slot, ing, bool(taken), reserved):
                continue
            grams = 0.0
            if need is not None:
                grams = self._contribution(
                    slot, ing.id, need, got, share, len(taken), servings
                )
                if grams <= 0:
                    continue
                self.draw(ing.id, grams)
                got += grams
            taken[ing.id] = grams
            self.claimed |= self.keys(ing.id)
        if share is not None and need is not None:
            got = self._top_up(taken, need, got, servings)

        ok = bool(taken) and (need is None or got >= need * MIN_SLOT_COVERAGE)
        if not ok:
            self.stock, self.claimed, self.consumed, self.made = snapshot
            taken = {}
        self.chosen[slot.id] = taken
        return ok

    def _skip(
        self, slot: ComponentSlot, ing: Ingredient, any_taken: bool, reserved: set[str]
    ) -> bool:
        """Leave this one out of the slot? Required ingredients never are."""
        if ing.id in reserved:
            return False
        if self.keys(ing.id) & self.claimed:
            return True  # already in the dish (the reuse rule)
        if (any_taken or slot.optional) and discouraged(ing, slot):
            # Beans can be the veg if there's nothing else, but not as well,
            # and an optional slot is better left empty
            return True
        # Nobody goes shopping for a garnish
        return slot.optional and self.needs_buying(ing.id)

    def _contribution(
        self,
        slot: ComponentSlot,
        ing_id: str,
        need: float,
        got: float,
        share: float | None,
        picked: int,
        servings: int,
    ) -> float:
        """Grams this ingredient should add to the slot; 0 means skip it."""
        grams = min(self.makeable(ing_id, servings), need - got)
        last_place = picked == slot.max_ingredients - 1
        if share is not None and not last_place:
            grams = min(grams, share)
        # The last place in the slot goes to something that finishes it,
        # so three capped aromatics can't crowd out the onion
        if last_place and got + grams < need * MIN_SLOT_COVERAGE:
            return 0.0
        return grams

    def _top_up(
        self, taken: dict[str, float], need: float, got: float, servings: int
    ) -> float:
        """Spread slots: if fair shares fell short, let what's there make up the rest."""
        for ing_id in taken:
            extra = min(self.makeable(ing_id, servings), need - got)
            if extra > 0:
                self.draw(ing_id, extra)
                taken[ing_id] += extra
                got += extra
        return got


def _expand_urgency(
    urgency: dict[str, float] | None, prepared_db: dict[str, PreparedIngredient]
) -> dict[str, float] | None:
    """A prepared form is as urgent as its most urgent base."""
    if urgency is None:
        return None
    out = dict(urgency)
    for prep in prepared_db.values():
        out[prep.id] = max(
            (urgency.get(b, 0.0) for b in prep.base_ingredients), default=0.0
        )
    return out


def _bias(
    stock: dict[str, float],
    db: dict[str, Ingredient],
    preps: dict[str, PreparedIngredient],
    affinity: dict[str, float] | None,
    buyable: Collection[str] | None,
) -> tuple[dict[str, float], set[str]]:
    """
    Learned taste plus the cost of buying. Makes anything buyable-but-not-in-stock
    available (mutates `stock`) and marks it down; prepared forms inherit the
    penalty of whichever bases would need buying.
    """
    bias = {i: VIBES_SCALE * a for i, a in (affinity or {}).items()}
    if not buyable:
        return bias, set()
    have_any = {i for i in db if have(i, stock, db) > 0}
    penalty = {i: buy_penalty(db[i]) for i in buyable if i in db and i not in have_any}
    for i, pen in penalty.items():
        stock[i] = INF
        bias[i] = bias.get(i, 0.0) - pen
    for prep in preps.values():
        extra = sum(penalty.get(b, 0.0) for b in prep.base_ingredients)
        if extra:
            bias[prep.id] = bias.get(prep.id, 0.0) - extra
    return bias, set(penalty)


def resolve(
    recipe: Recipe,
    inventory: Inventory,
    ingredient_db: dict[str, Ingredient] | None = None,
    prepared_db: dict[str, PreparedIngredient] | None = None,
    rng: random.Random | None = None,
    servings: int | None = None,
    urgency: dict[str, float] | None = None,
    affinity: dict[str, float] | None = None,
    buyable: Collection[str] | None = None,
) -> Resolution:
    """
    Resolve a recipe against an inventory.

        1. Build the pool: stock + unlimited staples + makeable prepared forms
        2. Every required ingredient must be in the pool, and is reserved for the
           slot that accepts it and scores it highest
        3. Each slot (in order) draws grams from the best-scoring candidates that
           don't break the reuse rule, until its amount is covered
        4. Required slots that can't reach MIN_SLOT_COVERAGE are unfillable

    servings defaults to recipe.serves. urgency (0..1 per ingredient id) boosts
    ingredients that are about to expire. buyable ingredients count as available
    in any amount but score worse than what's in stock (you'd have to buy them).
    affinity (-1..1 per ingredient id, from
    garde.vibes) nudges towards what you've liked in this kind of dish.
    """
    db = ingredient_db or INGREDIENTS
    preps = PREPARED_INGREDIENTS if prepared_db is None else prepared_db
    servings = servings or recipe.serves
    stock = normalise_stock(inventory, db)
    bias, to_buy = _bias(stock, db, preps, affinity, buyable)
    expanded_db = expand_inventory(stock, db, preps)
    pool = sorted(available_pool(stock, db, preps))
    pool_set = set(pool)
    urg = _expand_urgency(urgency, preps)
    failed = Resolution(recipe.id, recipe.name, [], set(), [], servings=servings)

    failed.missing_required = [i for i in recipe.required_ingredients if i not in pool]
    if failed.missing_required:
        return failed

    picks = _Assigner(recipe, db, preps, stock=dict(stock), to_buy=to_buy)

    # Reserve a slot for each required ingredient before anything else is picked
    reserved: dict[str, list[str]] = {slot.id: [] for slot in recipe.slots}
    for req in recipe.required_ingredients:
        ing = expanded_db[req]
        accepting = [
            slot
            for slot in recipe.slots
            if matches_slot(ing, slot, preps)
            and len(reserved[slot.id]) < slot.max_ingredients
            and not picks.keys(req) & picks.claimed
        ]
        if not accepting:
            failed.unplaceable_required.append(req)
            continue
        best = max(accepting, key=lambda sl, i=ing: score(i, sl, recipe, urg, bias))
        reserved[best.id].append(req)
        picks.claimed |= picks.keys(req)
    if failed.unplaceable_required:
        return failed

    assignments: list[SlotAssignment] = []
    for slot in recipe.slots:
        ranked = rank(
            [expanded_db[i] for i in slot_universe(slot, db, preps) if i in pool_set],
            slot,
            recipe,
            rng,
            urg,
            bias,
        )
        must = reserved[slot.id]
        ordered = [expanded_db[i] for i in must] + [
            i for i in ranked if i.id not in must
        ]
        ok = picks.fill(slot, ordered, slot_grams(slot, servings), set(must), servings)
        taken = picks.chosen[slot.id]
        assignments.append(
            SlotAssignment(
                slot_id=slot.id,
                slot_label=slot.label,
                assigned=list(taken),
                candidates=[ing.id for ing in ranked],
                filled=ok or slot.optional,
                grams={i: g for i, g in taken.items() if g},
            )
        )

    picks.sweep(stock, {a.slot_id: a.grams for a in assignments})
    used = {i for a in assignments for i in a.assigned}
    timing = recipe_timing(recipe, {a.slot_id: a.assigned for a in assignments}, preps)
    for raw in picks.made:
        made_from = db[raw].made_from
        if made_from is not None and made_from.time.total > 0:
            timing.make_ahead.append(raw)  # cook the beans; thinning cream isn't prep
    return Resolution(
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        assignments=assignments,
        used=used,
        unfillable=[a.slot_id for a in assignments if not a.filled],
        unplaceable_required=[r for r in recipe.required_ingredients if r not in used],
        consumed=Counter({k: v for k, v in picks.consumed.items() if v > 0}),
        servings=servings,
        timing=timing,
        made=Counter({k: v for k, v in picks.made.items() if v > 1e-9}),
    )


# ---------------------------------------------------------------------------
# Multi-recipe: score a list of recipes against inventory
# ---------------------------------------------------------------------------


@dataclass
class RecipeScore:
    recipe_id: str
    recipe_name: str
    resolution: Resolution
    score: float  # [0.0, 1.0], fraction of required slots filled


def rank_recipes(
    recipes: dict[str, Recipe],
    inventory: Inventory,
    ingredient_db: dict[str, Ingredient] | None = None,
    rng: random.Random | None = None,
    servings: int | None = None,
    urgency: dict[str, float] | None = None,
    feedback: Feedback | None = None,
    buyable: Collection[str] | None = None,
) -> list[RecipeScore]:
    """
    Resolve all recipes against inventory and rank by how completely they can be filled.
    Useful for the meal planner: given what I have, what can I actually make?
    """
    results = []
    for recipe in recipes.values():
        r = resolve(
            recipe,
            inventory,
            ingredient_db,
            rng=rng,
            servings=recipe.serves if recipe.batch else servings,
            urgency=urgency,
            affinity=feedback.ingredient_affinity(recipe) if feedback else None,
            buyable=buyable,
        )
        required_slots = [
            a for a in r.assignments if not _slot_is_optional(a.slot_id, recipe)
        ]
        filled = sum(1 for a in required_slots if a.filled)
        total = len(required_slots)
        if r.missing_required or r.unplaceable_required:
            recipe_score = 0.0
        else:
            recipe_score = filled / total if total else 1.0
        results.append(
            RecipeScore(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                resolution=r,
                score=recipe_score,
            )
        )
    return sorted(results, key=lambda rs: rs.score, reverse=True)


def _slot_is_optional(slot_id: str, recipe: Recipe) -> bool:
    for slot in recipe.slots:
        if slot.id == slot_id:
            return slot.optional
    return False


# ---------------------------------------------------------------------------
# Quick demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from garde.loader import RECIPES

    # We always got spices
    BASE_INVENTORY = [
        "garam_masala",
        "cumin",
        "coriander_seed",
        "chilli_powder",
        "smoked_paprika",
        "turmeric_dried",
        "curry_powder",
    ]

    # Inventory A — well stocked for green pasta
    inventory_a = [
        "spaghetti",
        "spinach",
        "peas",
        "cashews",
        "garlic",
        "lemon",
        "nutritional_yeast",
        "pine_nuts",
    ]

    # Inventory B — sparse, see what breaks
    inventory_b = [
        "farro",
        "kale",
        "cannellini_beans",
        "lemon",
    ]

    # Inventory C — can it find the creamy tomato lentil?
    inventory_c = [
        "spaghetti",
        "tomato",
        "red_lentils",
        "cashews",
        "onion",
        "garlic",
        "miso",
        "lemon",
        "pumpkin_seeds",
    ]

    for label, inv in [("A", inventory_a), ("B", inventory_b), ("C", inventory_c)]:
        print(f"\n{'=' * 60}")
        print(f"Inventory {label}: {inv}")
        print("=" * 60)
        ranked = rank_recipes(RECIPES, inv + BASE_INVENTORY)
        for rs in ranked:
            print(f"\n  [{rs.score:.0%}] {rs.resolution.summary()}")
