# arcipe

Archetype recipes: recipes as abstract graphs of *slots* ("a green sauce base",
"a curry protein") rather than fixed ingredient lists, so a week of meals can be
planned around whatever is actually in the cupboard — wasting as little as possible.

## Run it

```sh
uv run python -m arcipe.dashboard     # web dashboard on http://127.0.0.1:8765
uv run python -m arcipe.solver        # optimised week for a demo fridge
uv run python -m arcipe.planner       # greedy day-by-day planner, for comparison
uv run python -m arcipe.loader        # load the data and check references
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
| `dashboard.py` | Local web UI: fridge, knobs, plan, ratings |

### Data model

- **Ingredient** — roles it can play (`sauce_base`, `protein`, …), flavour, colour,
  season, cost, cuisine. Quantities: `unit_weight` (one carrot = 80g),
  `stock_ratio` (dried lentils swell: 0.4g dried per 1g cooked),
  `shelf_life_days`, `max_per_serving` (garlic caps at 8g a head),
  `unlimited` (pantry staple, assumed in stock), `staple` (may appear in several
  slots of one dish, e.g. flour).
- **Prepared ingredient** — ingredient + method = new roles: cashews → cashew cream.
  `base_ratio` says how much base it takes; `make_ahead` keeps soaks off the clock.
- **Slot** — the roles an ingredient must cover, acceptable prep methods, colour,
  `max_ingredients`, `amount` (grams on the plate per serving / per batch, or
  `trace`) and `time` (active + passive minutes).
- **Recipe** — slots plus edges between them. Cooking edges (`combine`, `fold_in`…)
  run in sequence; assembly edges (`coat`, `top`, `finish`) cook in parallel.
  `slot_overrides` tweaks a shared slot for one recipe:
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

### Taste

Ratings live in `~/.arcipe/feedback.yaml` (or `$ARCIPE_HOME`). They nudge
ingredient choice within a recipe (and, at half weight, within the same cuisine
family) and recipe choice in the solver. Scores are shrunk towards neutral, so one
bad night doesn't ban anything.
