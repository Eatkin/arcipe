# Garde Manger (package `garde`)

Plans a week of meals from what's in the cupboard, wasting as little food as possible.
Recipes are *archetypes*: graphs of abstract slots ("a curry protein", "a green sauce
base") that get filled from stock, rather than fixed ingredient lists. The household
mostly eats vegan, so prioritise vegan recipes.

README.md explains the data model, units, price and shopping, leftovers, and taste.
Read it before changing the data or the planner.

## Commands

```sh
uv run --extra dev pytest -q                     # ~1250 tests, ~16s
uv run --extra dev ruff check src tests tools    # lint (isort single-line imports, complexity ≤ 10)
uv run --extra dev ruff format src tests tools
uv run --extra dev ty check src                  # types
uv run python -m garde.loader                    # load the YAML and list dangling references
uv run python -m garde.solver                    # demo week
uv run python -m garde.dashboard                 # http://127.0.0.1:8765
```

Run lint, ty and pytest before every commit. Dependencies are pydantic and pyyaml
only. Add new ones only for tooling (for example scraping in `tools/`), and never
for the core package.

## Layout

| Path | What |
| --- | --- |
| `src/garde/models.py` | pydantic models, all `extra="forbid"`. Roles, classes, cuisines and methods are `Literal`s, so a new one must be added here |
| `src/garde/data/` | YAML: `ingredients/*.yaml`, `prepared.yaml`, `slots.yaml`, `recipes/*.yaml` |
| `resolver.py` | fills one recipe's slots from stock: gram ledger, reuse rule, made ingredients, scrap sweep |
| `planner.py` | lots with expiry, greedy planner, batch pots (`cook_batches`), leftover dishes |
| `solver.py` | simulated annealing over the week; scores by simulating with the real resolver |
| `timing.py`, `vibes.py`, `dashboard.py` + `static/dashboard.html` | DAG timing, learned taste from 👍/👎, stdlib web UI |
| `tools/corpus/` | scraping real recipes: `fetch.py` → `parse.py` → `analyse.py` |
| `corpus/` | scraped pages and `recipes.json`: **gitignored**, local only |
| `.scratch.md` | the user's running notes: ideas, follow-ups, done list. Keep it current |

## Working on the data

- Slot amounts are grams **as they land in the dish**. Stock is grams **as stocked**.
  `stock_ratio` (dried lentils 0.4), `base_ratio` (prepared forms) and
  `made_from.ratio` convert between the two.
- After any data change, run the loader and the full tests. `tests/test_data.py`
  checks that every recipe is makeable from plentiful stock. If it fails, the cause
  is usually a missing role, a missing class tag or a broken reference.
- Fixing a "weird pick" (peanut butter in Italian pasta, beans in the veg slot) is
  usually a data change: roles, classes, `cuisine_context`, `excluded_classes`, or
  `slot_overrides` on the recipe. Prefer soft scoring and learned taste over hard
  bans; the user's roasted-carrot coconut curry turned out nice.
- Drafted numbers (slot grams, times, shelf lives, `keeps_days`) are guesses
  waiting for the user's sanity check. Say when you've made new ones up.
- Beans are not `unlimited`: tins, dried beans (`dried_*`, cooked via `made_from`)
  or shopping must provide them.

## Recipe import (in progress)

Sources: the user's bookmarks in `../recipe-viewer/recipes.yaml`, and the whole
backlog of vegansandra.com (Blogger, sitemap at `/sitemap.xml`, 259 posts).

- `FETCH_DELAY=12 uv run python tools/corpus/fetch.py sandra` resumes the fetch;
  pages already cached are skipped.
- **As of 2026-11-25, only ~111 of 259 Sandra posts are cached.** The site returns
  429 after about 100 plain requests, even at 12 seconds apart.
- The user has said to use their scraping setup for this: undetected Chrome (add it
  to the tools with `uv add`), plus FlareSolverr if needed. Keep the request rate
  low anyway.
- Then:
  1. `uv run python tools/corpus/parse.py` writes `corpus/recipes.json`.
  2. `uv run python tools/corpus/analyse.py [--families]` shows which ingredients are
     missing and how recipes group into dish families.
- Turning a scraped recipe into an archetype: pick or define its slots, map its
  ingredients to ids, add any missing ingredients (roles, classes, amounts, shelf
  life, price), and record the URL in the recipe's `sources`. Prefer one archetype
  per dish family over one per post.

## The user

- Happy to hand over big chunks of work ("plough through").
- Wants work on a branch (currently `resolver-fixes`, not merged or pushed), with
  tested commits and a short plain-English summary at the end.
- Finds the ingredient granularity and tagging work tedious, so do it for them, but
  flag any judgement calls.
- Price is a 0–4 scale: 0 is spices, 1 carrots and cabbage, 2 cauliflower and
  broccoli, 3 tofu, 4 fancy. Words like "cheap" or "pricey" parse too.
