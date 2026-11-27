"""
garde.vibes
============
Learned taste. Rules like "roasted carrot doesn't go in curry" are wrong as often
as they're right, so instead of hard rules: rate meals you actually cooked
(+1 / -1) and let the ratings nudge future choices.

    ingredient affinity — did dishes with this ingredient *in this recipe* go well?
                          Ratings from other recipes of the same cuisine family
                          count at half weight, so a good roasted-carrot coconut
                          curry makes roasted carrot a bit more likely in a dal.
    recipe score        — did this recipe go well overall?

Both are shrunk towards 0 (neutral) with a pseudo-count, so one bad night
doesn't blacklist anything: one 👎 gives -1/3, three give -3/5.

Stored as YAML at $GARDE_HOME/feedback.yaml (default ~/.garde).
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from pathlib import Path

import yaml

from garde.models import CUISINE_FAMILY
from garde.models import Recipe
from garde.utils import garde_home


# Pseudo-count of neutral ratings every score starts with
SHRINK = 2.0
# Weight of a rating from a different recipe in the same cuisine family
FAMILY_WEIGHT = 0.5


def default_path() -> Path:
    return garde_home() / "feedback.yaml"


@dataclass
class Rating:
    recipe_id: str
    ingredients: list[str]  # everything that was in the dish
    rating: int  # +1 or -1
    when: str = field(default_factory=lambda: date.today().isoformat())
    note: str = ""


@dataclass
class Feedback:
    ratings: list[Rating] = field(default_factory=list)
    recipes: dict[str, Recipe] = field(default_factory=dict)  # for cuisine lookups

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None, recipes: dict[str, Recipe] | None = None):
        path = path or default_path()
        raw = yaml.safe_load(path.read_text()) if path.exists() else None
        ratings = [Rating(**r) for r in (raw or {}).get("ratings", [])]
        return cls(ratings=ratings, recipes=recipes or {})

    def save(self, path: Path | None = None) -> Path:
        path = path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                {"ratings": [asdict(r) for r in self.ratings]}, sort_keys=False
            )
        )
        return path

    def add(
        self, recipe_id: str, ingredients: list[str], rating: int, note: str = ""
    ) -> Rating:
        if rating not in (1, -1):
            raise ValueError("rating is +1 or -1")
        r = Rating(recipe_id, sorted(set(ingredients)), rating, note=note)
        self.ratings.append(r)
        return r

    def remove(self, recipe_id: str, rating: int) -> bool:
        """Take back the newest matching rating (a vote changed or undone)."""
        for i in range(len(self.ratings) - 1, -1, -1):
            r = self.ratings[i]
            if r.recipe_id == recipe_id and r.rating == rating:
                del self.ratings[i]
                return True
        return False

    # -- scores --------------------------------------------------------------

    def _families(self, recipe_id: str) -> set[str]:
        recipe = self.recipes.get(recipe_id)
        if recipe is None:
            return set()
        return {CUISINE_FAMILY[c] for c in recipe.cuisine_context} - {"universal"}

    def recipe_score(self, recipe_id: str) -> float:
        """-1..1: how this recipe has gone down overall."""
        mine = [r.rating for r in self.ratings if r.recipe_id == recipe_id]
        return sum(mine) / (len(mine) + SHRINK)

    def ingredient_affinity(self, recipe: Recipe) -> dict[str, float]:
        """-1..1 per ingredient id: how it has gone down in this kind of dish."""
        families = {CUISINE_FAMILY[c] for c in recipe.cuisine_context} - {"universal"}
        total: dict[str, float] = {}
        weight: dict[str, float] = {}
        for r in self.ratings:
            if r.recipe_id == recipe.id:
                w = 1.0
            elif families and families & self._families(r.recipe_id):
                w = FAMILY_WEIGHT
            else:
                continue
            for ing in r.ingredients:
                total[ing] = total.get(ing, 0.0) + w * r.rating
                weight[ing] = weight.get(ing, 0.0) + w
        return {ing: total[ing] / (weight[ing] + SHRINK) for ing in total}
