# Garde Manger (package `garde`)

Plans a week of meals from what's in the cupboard, wasting as little food as possible.
Recipes are *archetypes*: graphs of abstract slots ("a curry protein", "a green sauce
base") that get filled from stock, rather than fixed ingredient lists. The household
mostly eats vegan, so prioritise vegan recipes.

README.md explains the data model, units, price and shopping, leftovers, and taste.
Read it before changing the data or the planner.

## Commands

```sh
uv run --extra dev pytest -q                     # ~1950 tests, ~35s
uv run --extra dev --extra ui pytest tests/ui    # browser smoke test (skips without Playwright)
uv run --extra dev ruff check src tests tools    # lint (isort single-line imports, complexity ≤ 10)
uv run --extra dev ruff format src tests tools
uv run --extra dev ty check src                  # types
uv run python -m garde.loader                    # load the YAML and list dangling references
uv run python -m garde.solver                    # demo week
uv run python -m garde.dashboard                 # http://127.0.0.1:8765
uv run python -m garde.dashboard --host 0.0.0.0  # let a phone on the home wifi in (no login)
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
| `timing.py`, `vibes.py`, `method.py` | DAG timing, learned taste from 👍/👎, cooking outline from the slot graph |
| `pantry.py`, `plans.py` | saved pantry with cooked and frozen dishes (`$GARDE_HOME/pantry.json`), binned waste (`waste.json`), staples not kept (`household.json`); saved plans, ticking off, re-planning the rest of the week (`$GARDE_HOME/plans/`) |
| `dashboard.py` + `static/dashboard.{html,css,js}` | stdlib web UI with SSE search progress. Pantry saves carry a `version` (409 from a stale tab or device). `tests/ui/` drives it with Playwright (`ui` extra, skipped without it) |
| `units.py` | display amounts: liquids (an ingredient with a `density`, g per ml) show in ml / l, the ledger stays grams |
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
- `discouraged_classes` on a slot is the soft way to keep things out (beans in
  "Veg"); `excluded_classes` is the hard way. `opened_days` sets how long a tin
  or a cut vegetable keeps once broken into.
- `density` on an ingredient makes it a liquid (shown and typed in ml). Thick sauces,
  passata and nut butters stay in grams. Pantry amounts also take words ("2 tins",
  "half a bag", "2 x 400g"): see `pantry._measured`.
- Beans are not `unlimited`: tins, dried beans (`dried_*`, cooked via `made_from`)
  or shopping must provide them.

## Recipe import (in progress)

Sources: the user's bookmarks in `../recipe-viewer/recipes.yaml`, and the whole
backlog of vegansandra.com (Blogger, sitemap at `/sitemap.xml`, 259 posts).

- **All 259 Sandra posts are cached (2026-11-25)**; 209 parse as recipes. The
  other 50 are mostly listicles, videos and announcements, but a few real recipes
  (pierogi, cauliflower stew with white beans, rhubarb cake) defeat the `parse.py`
  heuristic. `labels` comes out empty for every Sandra post (the `rel='tag'`
  regex doesn't match).
- Plain fetching (`fetch.py sandra`) gets Google's block after ~100 requests:
  302 to `google.com/sorry`, a reCAPTCHA, not Cloudflare, so FlareSolverr won't
  help. Use `FETCH_DELAY=20 uv run --extra scrape python tools/corpus/fetch.py
  sandra --browser` (`undetected-chromedriver`, the `scrape` extra). On a block it
  tries the post's `?m=1` copy (usually not blocked), then pops a desktop
  notification and waits `HUMAN_WAIT` seconds (default 300) for the user to solve
  the captcha in the Chrome window, then skips. Rerun to retry skipped posts.
- Don't use the PyPI package named `undetected` (the user's pokemon scraper has
  0.0.12): that release has gone, and the name now holds a 1.0.1 from a different
  author, which looks like a takeover.
- Then:
  1. `uv run python tools/corpus/parse.py` writes `corpus/recipes.json`.
  2. `uv run python tools/corpus/analyse.py [--families]` shows which ingredients are
     missing and how recipes group into dish families.
- **Sandra is imported (2026-11-25)**: 196 posts are `sources` on archetypes; the rest
  are desserts, menus, news and components. Never type a Sandra URL from a title (the
  year in the path is the post date, and earlier guesses were wrong): take it from
  `corpus/index.json`.
- **Pinch of Nom (2026-11-25)**: `fetch.py pinchofnom` crawls the vegan + vegetarian
  category listings once into `corpus/pinchofnom-urls.json` (1125 recipes), then
  fetches only `tools/corpus/pinchofnom_picks.txt`. Plain urllib works, no blocks
  at 9 s apart. About half the recipes are members-only (empty `pon-void` recipe
  block): don't try to get around that, and don't cite them as sources. Pick for
  new dish families and gaps, not volume.
- One-off pages from any site go in `tools/corpus/extra_urls.txt` (`fetch.py extra`).
  Some sites 403 plain fetches; plenty of others don't, so just pick another.
- `undetected-chromedriver` is pinned to 3.5.5 (the original author's last release).
- Salt and water aren't ingredients: every kitchen has them (`ASSUMED` in analyse.py).
- Turning a scraped recipe into an archetype: pick or define its slots, map its
  ingredients to ids, add any missing ingredients (roles, classes, amounts, shelf
  life, price), and record the URL in the recipe's `sources`. Prefer one archetype
  per dish family over one per post.

## The user

- Happy to hand over big chunks of work ("plough through").
- Wants work on a branch (`resolver-fixes` went to master via PR #1, 2026-11-27; start a new branch for new work), with
  tested commits and a short plain-English summary at the end.
- Finds the ingredient granularity and tagging work tedious, so do it for them, but
  flag any judgement calls.
- Price is a 0–4 scale: 0 is spices, 1 carrots and cabbage, 2 cauliflower and
  broccoli, 3 tofu, 4 fancy. Words like "cheap" or "pricey" parse too.
