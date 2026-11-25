# Garde Manger

The *garde manger* is the kitchen brigade's keeper of the larder: the station that
knows what's in stock and makes sure it gets used. The package is `garde`.

Archetype recipes: recipes as abstract graphs of *slots* ("a green sauce base",
"a curry protein") rather than fixed ingredient lists, so a week of meals can be
planned around whatever is actually in the cupboard — wasting as little as possible.

## Run it

```sh
uv run python -m garde.dashboard     # web dashboard on http://127.0.0.1:8765
uv run python -m garde.solver        # optimised week for a demo fridge
uv run python -m garde.planner       # greedy day-by-day planner, for comparison
uv run python -m garde.loader        # load the data and check references
uv run --extra dev pytest             # tests
```

## How it fits together

| Module | Job |
| --- | --- |
| `data/` | YAML: ingredients, prepared forms, slots, recipes |
| `loader.py` | Loads and validates the YAML (unknown keys are errors) |
| `resolver.py` | Fills one recipe's slots from stock: roles, colour, method, cuisine, quantities |
| `timing.py` | Total and hands-on time from the recipe graph |
| `planner.py` | Stock as lots with expiry dates; greedy day-by-day planning; waste |
| `solver.py` | Whole-week search, scoring each candidate week by simulating it |
| `vibes.py` | Learns taste from 👍/👎 ratings |
| `method.py` | A rough cooking outline from a recipe's slot graph |
| `pantry.py` | What's in the kitchen (ingredients and cooked dishes), saved between visits |
| `plans.py` | Saved plans, and ticking off what you cooked |
| `dashboard.py` + `static/` | Local web UI: pantry, settings, live search progress, plan, how to cook, ratings |

### Data model

- **Ingredient** — roles it can play (`sauce_base`, `protein`, …), flavour, colour,
  season, cost, cuisine. Quantities: `unit_weight` (one carrot = 80g),
  `stock_ratio` (dried lentils swell: 0.4g dried per 1g cooked),
  `shelf_life_days`, `max_per_serving` (garlic caps at 8g a head),
  `storage` (fridge / freezer / cupboard / counter; worked out from the file and
  shelf life if not given), `unlimited` (pantry staple, assumed in stock), `staple` (may appear in several
  slots of one dish, e.g. flour), `made_from` (see below), `portion` (smallest
  amount worth keeping).
- **Prepared ingredient** — ingredient + method = new roles: cashews → cashew cream.
  `base_ratio` says how much base it takes; `make_ahead` keeps soaks off the clock.
- **Slot** — the roles an ingredient must cover, acceptable prep methods, colour,
  `max_ingredients`, `amount` (grams on the plate per serving / per batch, or
  `trace`) and `time` (active + passive minutes).
- **Recipe** — slots plus edges between them. Cooking edges (`combine`, `fold_in`…)
  run in sequence; assembly edges (`coat`, `top`, `finish`) cook in parallel.
  `batch: true` makes a whole batch of `serves` portions whoever's eating (a loaf);
  `keeps_days` says how long leftovers last. `slot_overrides` tweaks a shared slot
  for one recipe:
  ```yaml
  green_grain_bowl:
    slot_overrides:
      grain_base: {required_method: [boil, steam]}
  ```

### Units

Slot amounts are grams **as they land in the dish**. Stock is grams **as stocked**.
`stock_ratio` and `base_ratio` convert between them. Inventory can be given as
counts (`carrot: 6`), measures (`red_lentils: "500g"`), and with explicit expiry
(`spinach: {amount: 1, expires: 2}`).

### Made ingredients, scraps and leftovers

- **Made from something else.** `chickpeas` has `made_from: {ingredient:
  dried_chickpeas, ratio: 0.42, batch: 600, keeps_days: 4}`. Stocked chickpeas
  (tins, or a pot you cooked earlier) are used first; otherwise they're cooked from
  dried, a whole pot at a time, and the rest of the pot goes in the fridge as its
  own stock with its own expiry. Coconut milk is made from coconut cream the same
  way, with no batch: you thin what you need.
- **Scraps.** If cooking would leave less than `Ingredient.crumb` of something
  perishable (default half a unit, at most 50g, else 15g), it goes in the dish.
- **Leftovers.** A batch dish (`batch: true`) makes all its portions at once, so
  one banana bread covers several breakfasts. The solver can also cook a dish that
  keeps for twice the people (`chilli*2`) and plan a later meal as leftovers. Spare
  portions keep for `keeps_days`; after that they count as waste. Eating
  leftovers costs 5 minutes and the small `leftovers` weight, not a repeat.

### Price and shopping

Every ingredient has a `price` from 0 to 4, written as a number or a word:

| 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- |
| negligible / free | cheap / budget | moderate / normal | pricey / expensive | fancy / luxury |
| spices, stock | carrots, cabbage, tins | cauliflower, broccoli | tofu, cashews | pine nuts |

It's per pack (`pack_weight`, else `unit_weight`, else 500g). Cheaper things score
a little higher when filling slots. With `shopping=True` (the dashboard's "Shop for
what's missing"), the solver may buy whole packs on a single day-1 shop: the spend
(price × packs) counts against the plan via the `price` weight, leftovers from a
pack stay in stock for later meals (or go to waste), fresh things can't be bought
for meals after they'd have gone off, and optional slots never trigger a purchase.
`plan.shopping_list()` prints the list.

### Taste

Ratings live in `~/.garde/feedback.yaml` (or `$GARDE_HOME`). They nudge
ingredient choice within a recipe (and, at half weight, within the same cuisine
family) and recipe choice in the solver. Scores are shrunk towards neutral, so one
bad night doesn't ban anything.

### Dashboard

`uv run python -m garde.dashboard`, then http://127.0.0.1:8765.

- **Pantry** tab: what's in, by fridge / freezer / cupboard / counter. Each item
  has an amount and a use-by date (the last good day). It's saved to
  `~/.garde/pantry.json` (or `$GARDE_HOME`) and plans start from it. Days left are
  counted from the plan's start date, and anything past its date is ignored.
  **Cooked, in the fridge** lists leftover dishes (portions and a use-by); plans
  serve them as leftovers meals before cooking anything new.
- **Saved plans** tab: **Save plan** keeps the shown week in
  `~/.garde/plans/<id>.json`. Tick a meal off as **Cooked** and its ingredients
  come out of the pantry (the grams the plan worked out, soonest use-by first,
  anything not listed skipped). The rest of a pot of beans goes in as cooked beans,
  and portions beyond the people eating go in as a dish if the recipe keeps.
  Ticking a leftovers meal as **Eaten** takes its portions out. Unticking puts it
  all back.
- **Plan** tab: settings on the left, with the solver weights as plain sliders
  ("Use up food: don't mind binning a bit ↔ use every last scrap"). Weeknights and
  weekends have separate time limits. Nothing runs until you press **Plan the
  week**. The search streams its progress (Server-Sent Events on
  `/api/plan/stream`), and **Stop** cancels it.
- Each meal has **How to make it**: the description, a rough outline built from
  the slot graph (`method.py`), the notes, and the source recipes to cook from.
