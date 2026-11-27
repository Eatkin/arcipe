"""
What does the corpus need that garde doesn't have?

    uv run python tools/corpus/analyse.py            # unmapped ingredients by frequency
    uv run python tools/corpus/analyse.py --families # recipes grouped into dish families

Ingredient lines are normalised ("2 small or one large carrot (grated)" -> "carrot")
and mapped to garde ingredient ids through the ingredient names plus ALIASES.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections import defaultdict
from pathlib import Path

from garde.loader import INGREDIENTS
from garde.loader import PREPARED_INGREDIENTS


ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / "corpus" / "recipes.json"

FRACTIONS = "½¼¾⅓⅔⅛⅜⅝⅞"
UNITS = (
    r"cups?|c\.|tbsps?|tbs|tsps?|tl|tablespoons?|teaspoons?|g|grams?|kg|ml|dl|l|litres?|"
    r"liters?|oz|ounces?|lbs?|pounds?|cans?|tins?|jars?|packs?|packets?|blocks?|cloves?|"
    r"pinch(?:es)?|handfuls?|bunch(?:es)?|sprigs?|stalks?|sticks?|slices?|pieces?|heads?|"
    r"inch(?:es)?|cm|dash(?:es)?|splash(?:es)?|drops?|bags?|scoops?|knobs?|thumbs?"
)
SIZES = r"heaping|heaped|level|scant|generous|large|medium|small|big|extra|about|approx\.?|roughly|almost|a few|few|some|a|an|of|x"
PREP = (
    r"finely|roughly|thinly|freshly|coarsely|chopped|diced|minced|grated|sliced|crushed|"
    r"peeled|drained|rinsed|cooked|uncooked|raw|soaked|frozen|fresh|halved|quartered|"
    r"shredded|cubed|mashed|melted|softened|toasted|roasted|ground|whole|organic|"
    r"unsweetened|plain|homemade|store[- ]bought|vegan|optional|divided|packed|"
    r"canned|tinned|dried|dry|boiling|cold|warm|hot|room temperature|to taste|for serving|"
    r"for garnish|plus more|or more|as needed|cut into.*|into .*|trimmed|deseeded|seeded|"
    r"pressed|firm|extra[- ]firm|light|full[- ]fat"
)

# corpus phrase -> garde id. Names of existing ingredients match automatically.
ALIASES: dict[str, str] = {
    "garlic clove": "garlic",
    "clove garlic": "garlic",
    "onions": "onion",
    "yellow onion": "onion",
    "red onion": "onion",
    "white onion": "onion",
    "brown onion": "onion",
    "spring onion": "green_onion",
    "spring onions": "green_onion",
    "scallion": "green_onion",
    "scallions": "green_onion",
    "green onions": "green_onion",
    "zucchini": "courgette",
    "courgettes": "courgette",
    "carrots": "carrot",
    "potatoes": "potato",
    "sweet potatoes": "sweet_potato",
    "tomatoes": "tomato",
    "cherry tomatoes": "tomato",
    "crushed tomatoes": "canned_tomato",
    "diced tomatoes": "canned_tomato",
    "tomato sauce": "passata",
    "tomato passata": "passata",
    "passata": "passata",
    "tomato paste": "tomato_puree",
    "tomato purée": "tomato_puree",
    "chickpea": "chickpeas",
    "garbanzo beans": "chickpeas",
    "kidney bean": "kidney_beans",
    "red kidney beans": "kidney_beans",
    "white beans": "cannellini_beans",
    "cannellini": "cannellini_beans",
    "red lentil": "red_lentils",
    "lentils": "green_lentils",
    "green lentils": "green_lentils",
    "brown lentils": "green_lentils",
    "oil": "vegetable_oil",
    "vegetable oil": "vegetable_oil",
    "sunflower oil": "vegetable_oil",
    "rapeseed oil": "vegetable_oil",
    "canola oil": "vegetable_oil",
    "neutral oil": "vegetable_oil",
    "extra virgin olive oil": "olive_oil",
    "coconut oil": "coconut_oil",
    "salt": "salt",
    "sea salt": "salt",
    "salt and pepper": "salt",
    "black pepper": "black_pepper",
    "pepper": "black_pepper",
    "water": "water",
    "vegetable broth": "vegetable_stock",
    "vegetable stock": "vegetable_stock",
    "broth": "vegetable_stock",
    "stock": "vegetable_stock",
    "flour": "plain_flour",
    "all-purpose flour": "plain_flour",
    "all purpose flour": "plain_flour",
    "wheat flour": "plain_flour",
    "nutritional yeast flakes": "nutritional_yeast",
    "nooch": "nutritional_yeast",
    "soy sauce": "soy_sauce",
    "tamari": "soy_sauce",
    "coconut milk": "coconut_milk",
    "plant milk": "plant_milk",
    "soy milk": "plant_milk",
    "oat milk": "plant_milk",
    "almond milk": "plant_milk",
    "non-dairy milk": "plant_milk",
    "soy cream": "plant_cream",
    "oat cream": "plant_cream",
    "vegan cream": "plant_cream",
    "cream": "plant_cream",
    "maple syrup": "maple_syrup",
    "agave syrup": "maple_syrup",
    "sugar": "sugar",
    "brown sugar": "sugar",
    "cane sugar": "sugar",
    "lemon juice": "lemon",
    "lime juice": "lime",
    "limes": "lime",
    "cilantro": "coriander_leaf",
    "coriander": "coriander_leaf",
    "parsley": "parsley",
    "basil": "basil",
    "dill": "dill",
    "thyme": "thyme",
    "oregano": "oregano",
    "bay leaf": "bay_leaf",
    "bay leaves": "bay_leaf",
    "paprika powder": "paprika",
    "sweet paprika": "paprika",
    "smoked paprika powder": "smoked_paprika",
    "cumin seeds": "cumin",
    "cumin powder": "cumin",
    "coriander powder": "coriander_seed",
    "coriander seeds": "coriander_seed",
    "turmeric": "turmeric_dried",
    "turmeric powder": "turmeric_dried",
    "garam masala powder": "garam_masala",
    "chili powder": "chilli_powder",
    "chilli powder": "chilli_powder",
    "cayenne pepper": "chilli_powder",
    "cayenne": "chilli_powder",
    "red pepper flakes": "chilli_flakes",
    "chili flakes": "chilli_flakes",
    "chilli flakes": "chilli_flakes",
    "cinnamon": "cinnamon_ground",
    "baking soda": "bicarbonate_of_soda",
    "bell pepper": "bell_pepper",
    "red bell pepper": "bell_pepper",
    "red pepper": "bell_pepper",
    "green bell pepper": "bell_pepper",
    "bell peppers": "bell_pepper",
    "mushroom": "mushrooms",
    "button mushrooms": "mushrooms",
    "champignons": "mushrooms",
    "pasta": "pasta",
    "spaghetti": "spaghetti",
    "macaroni": "pasta",
    "penne": "pasta",
    "rice": "basmati_rice",
    "white rice": "basmati_rice",
    "jasmine rice": "basmati_rice",
    "rice noodles": "rice_noodles",
    "noodles": "noodles",
    "tofu": "tofu",
    "cashew": "cashews",
    "cashew nuts": "cashews",
    "raw cashews": "cashews",
    "peanut butter": "peanut_butter",
    "tahini": "tahini",
    "ginger": "ginger",
    "fresh ginger": "ginger",
    "ginger root": "ginger",
    "frozen peas": "peas",
    "green peas": "peas",
    "broccoli": "broccoli",
    "cauliflower florets": "cauliflower",
    "cabbage": "cabbage",
    "white cabbage": "cabbage",
    "red cabbage": "cabbage",
    "celery": "celery",
    "celery stalk": "celery",
    "leek": "leek",
    "leeks": "leek",
    "eggplant": "aubergine",
    "aubergines": "aubergine",
    "beetroot": "beetroot",
    "beets": "beetroot",
    "corn": "sweetcorn",
    "sweet corn": "sweetcorn",
    "corn kernels": "sweetcorn",
    "baking powder": "baking_powder",
    "yeast": "yeast",
    "instant yeast": "yeast",
    "dry yeast": "yeast",
    "mustard": "mustard",
    "dijon mustard": "mustard",
    "vinegar": "white_wine_vinegar",
    "apple cider vinegar": "apple_cider_vinegar",
    "rice vinegar": "rice_vinegar",
    "sesame oil": "sesame_oil",
    "toasted sesame oil": "sesame_oil",
    "sesame seeds": "sesame_seeds",
    "oats": "oats",
    "rolled oats": "oats",
    "oat flakes": "oats",
    "breadcrumbs": "breadcrumbs",
    "bread crumbs": "breadcrumbs",
    "panko": "breadcrumbs",
    "vegan butter": "butter",
    "margarine": "butter",
    "vegan margarine": "butter",
    "bananas": "banana",
    "apples": "apple",
    "chilli": "fresh_chilli",
    "chili": "fresh_chilli",
    "chili pepper": "fresh_chilli",
    "jalapeño": "fresh_chilli",
    "jalapeno": "fresh_chilli",
    "red chili": "fresh_chilli",
    "green chili": "fresh_chilli",
    "kale": "kale",
    "spinach leaves": "spinach",
    "baby spinach": "spinach",
    "lemons": "lemon",
    "walnuts": "walnuts",
    "almonds": "almonds",
    "sunflower seed": "sunflower_seeds",
    "pumpkin seed": "pumpkin_seeds",
    "flaxseed": "flaxseed",
    "ground flaxseed": "flaxseed",
    "flax seeds": "flaxseed",
    "chia seeds": "chia_seeds",
    "cornstarch": "cornflour",
    "corn starch": "cornflour",
    "cornflour": "cornflour",
    "potato starch": "cornflour",
    "starch": "cornflour",
    "tapioca starch": "cornflour",
    "buckwheat": "buckwheat",
    "quinoa": "quinoa",
    "couscous": "couscous",
    "bulgur": "bulgur",
    "pearl barley": "pearl_barley",
    "barley": "pearl_barley",
    "smoked tofu": "tofu",
    "tempeh": "tempeh",
    "seitan": "seitan",
    "soy chunks": "soy_chunks",
    "tvp": "soy_chunks",
    "textured vegetable protein": "soy_chunks",
    "black beans": "black_beans",
    "pinto beans": "kidney_beans",
    "liquid smoke": "liquid_smoke",
    "miso paste": "miso",
    "white miso": "miso",
    "gochujang": "gochujang",
    "sriracha": "hot_sauce",
    "hot sauce": "hot_sauce",
    "sweet chili sauce": "sweet_chilli_sauce",
    "ketchup": "tomato_ketchup",
    "vanilla extract": "vanilla",
    "vanilla": "vanilla",
    "cocoa powder": "cocoa",
    "cocoa": "cocoa",
    "raisins": "raisins",
    "dates": "dates",
    "garlic powder": "garlic_powder",
    "onion powder": "onion_powder",
    "nutmeg": "nutmeg",
    "cloves": "cloves_whole",
    "cardamom": "cardamom",
    "cardamom pods": "cardamom",
    "mustard seeds": "mustard_seeds",
    "fennel seeds": "fennel_seeds",
    "curry leaves": "curry_leaves",
    "kasuri methi": "fenugreek_seed",
    "fenugreek": "fenugreek_seed",
    "asafoetida": "asafoetida",
    "hing": "asafoetida",
    "rosemary": "rosemary",
    "chives": "chives",
    "mint": "mint",
    "pickles": "pickles",
    "pickled cucumbers": "pickles",
    "cucumber": "cucumber",
    "cucumbers": "cucumber",
    "avocado": "avocado",
    "avocados": "avocado",
    "lettuce": "lettuce",
    "rocket": "lettuce",
    "arugula": "lettuce",
    "radish": "radish",
    "radishes": "radish",
    "pumpkin": "squash",
    "butternut squash": "squash",
    "squash": "squash",
    "brussels sprouts": "brussels_sprouts",
    "asparagus": "asparagus",
    "green beans": "green_beans",
    "olives": "olives",
    "black olives": "olives",
    "kalamata olives": "olives",
    "sauerkraut": "sauerkraut",
    "tortillas": "tortillas",
    "tortilla": "tortillas",
    "bread": "bread",
    "buns": "bread",
    "puff pastry": "puff_pastry",
    "beans": "kidney_beans",
    "canned beans": "kidney_beans",
    "brown beans": "kidney_beans",
    "mayo": "mayo",
    "mayonnaise": "mayo",
    "vegan mayo": "mayo",
    "milk": "plant_milk",
    "dairy free milk of choice": "plant_milk",
    "parmesan": "vegan_parmesan",
    "cane syrup": "maple_syrup",
    "golden syrup": "maple_syrup",
    "green chilies": "fresh_chilli",
    "green chillies": "fresh_chilli",
    "marinara sauce": "passata",
    "satay sauce": "peanut_sauce",
    "beet cubes": "beetroot",
    "beet": "beetroot",
    "herbs": "mixed_herbs",
    "mixed herbs": "mixed_herbs",
    "italian herbs": "mixed_herbs",
    "italian herb blend": "mixed_herbs",
    "marjoram": "mixed_herbs",
    "yogurt": "plant_yogurt",
    "non-dairy yogurt": "plant_yogurt",
    "soy yogurt": "plant_yogurt",
    "shallot": "onion",
    "shallots": "onion",
    "black peppercorns": "black_pepper",
    "nori sheets": "nori",
    "nori": "nori",
    "swiss chard leaves": "swiss_chard",
    "swiss chard": "swiss_chard",
    "chard": "swiss_chard",
    "millet flakes": "millet",
    "cherries": "frozen_fruit",
    "pitted cherries": "frozen_fruit",
    "berries": "frozen_fruit",
    "seasonal salad leaves": "lettuce",
    "salad leaves": "lettuce",
    "vegetables": "?skip",
    "eggs": "?skip",
    "people": "?skip",
    "sandra": "?skip",
    "div class": "?skip",
    "sauce": "?skip",
    # added with the full Sandra corpus
    "soy mince": "soy_chunks",
    "soy flakes": "soy_chunks",
    "chanterelles": "mushrooms",
    "white mushrooms": "mushrooms",
    "dark chocolate": "cocoa",
    "orange juice": "orange",
    "oranges": "orange",
    "plums": "prunes",
    "dried plums": "prunes",
    "rhubarb": "rhubarb",
    "root celery": "celeriac",
    "celery root": "celeriac",
    "celeriac": "celeriac",
    "rutabaga": "turnip",
    "swede": "turnip",
    "tomato juice": "passata",
    "vegetable stock powder": "vegetable_stock",
    "chickpea flour": "gram_flour",
    "gram flour": "gram_flour",
    "besan": "gram_flour",
    "buckwheat flour": "buckwheat",
    "tempura flour": "plain_flour",
    "seaweed": "nori",
    "barbecue sauce": "barbecue_sauce",
    "bbq sauce": "barbecue_sauce",
    "your favorite barbecue sauce": "barbecue_sauce",
    "sour cream": "plant_yogurt",
    "sunflower sour cream": "sunflower_cream",
    "raspberries": "frozen_fruit",
    "blueberries": "frozen_fruit",
    "balsamic vinegar": "white_wine_vinegar",
    "soy": "soy_sauce",
    "black": "?skip",
    "brown": "?skip",
    "green": "green_lentils",
}

# Things every kitchen has: not worth an ingredient, never counted as missing
ASSUMED = {"salt", "water", "ice", "?skip"}


def normalise(line: str) -> str:
    s = line.lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)  # parentheticals
    s = re.sub(r"^[\s\-•*–]+", "", s)  # noqa: RUF001 — en dashes really appear in recipes
    s = s.split(",")[0]
    s = re.split(r"\s+or\s+|\s*/\s*(?=[a-z])", s)[0]
    s = re.sub(rf"[\d{FRACTIONS}][\d{FRACTIONS}.,/\-–+ ]*", " ", s)  # noqa: RUF001 — quantities; en dashes appear in ranges
    s = re.sub(rf"\b({UNITS})\b\.?", " ", s)
    s = re.sub(rf"\b({PREP})\b", " ", s)
    for _ in range(2):
        s = re.sub(rf"^\s*({SIZES})\b", " ", s)
    s = re.sub(r"[^a-zäöüéñ\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s


def lookup() -> dict[str, str]:
    table = {}
    for ing_id, ing in INGREDIENTS.items():
        table[ing_id.replace("_", " ")] = ing_id
        table[ing.name.lower()] = ing_id
        table[re.sub(r"\s*\(.*\)", "", ing.name.lower())] = ing_id
    table.update(ALIASES)
    return table


def match(name: str, table: dict[str, str]) -> str | None:
    if not name:
        return None
    candidates = [name, name.rstrip("s"), name + "s", name.replace("-", " ")]
    for c in candidates:
        if c in table:
            return table[c]
    # longest known phrase contained in the name ("smoked paprika powder")
    hits = [
        (len(k), v) for k, v in table.items() if re.search(rf"\b{re.escape(k)}\b", name)
    ]
    return max(hits)[1] if hits else None


def main() -> None:
    recipes = json.loads(RECIPES.read_text())
    table = lookup()
    known = Counter()
    unknown = Counter()
    unknown_ids = Counter()
    examples: dict[str, str] = {}
    for r in recipes:
        seen_known, seen_unknown = set(), set()
        for line in r["ingredients"]:
            name = normalise(line)
            if not name or len(name) > 40:
                continue
            hit = match(name, table)
            if hit in ASSUMED:
                continue
            if hit and (hit in INGREDIENTS or hit in PREPARED_INGREDIENTS):
                seen_known.add(hit)
            elif hit:
                seen_unknown.add(hit)
                examples.setdefault(hit, line)
            else:
                seen_unknown.add("?" + name)
                examples.setdefault("?" + name, line)
        known.update(seen_known)
        for u in seen_unknown:
            (unknown_ids if not u.startswith("?") else unknown)[u] += 1
    n = len(recipes)
    print(f"{n} recipes\n")
    print("Aliased but not in garde yet (recipes using it):")
    for k, c in unknown_ids.most_common():
        print(f"  {c:>4}  {k:<22} e.g. {examples[k][:60]}")
    print("\nUnrecognised (top 80):")
    for k, c in unknown.most_common(80):
        print(f"  {c:>4}  {k[1:]:<30} e.g. {examples[k][:60]}")
    print("\nAlready in garde (top 30):")
    print("  " + ", ".join(f"{k} {c}" for k, c in known.most_common(30)))


FAMILIES = {
    "curry / dal": r"curry|dal\b|dhal|masala|korma|tikka|madras|saag|vindaloo|pyaza|pepper fry",
    "pasta": r"pasta|spaghetti|macaroni|\bmac\b|lasagn|penne|noodle(?!.*salad)|gnocchi",
    "noodles / stir fry": r"noodle|stir[- ]fry|pad thai|chow mein|lo mein|teriyaki|sweet chili",
    "soup": r"soup|chowder|borscht|bisque|broth",
    "stew / chilli": r"stew|chili|chilli|goulash|ragout|casserole|tagine|gravy|tuscan",
    "rice / grain": r"rice|risotto|pilaf|plov|paella|quinoa|buckwheat|bulgur|couscous|barley|bowl",
    "salad": r"salad|slaw",
    "patties / burgers": r"burger|patt(y|ies)|cutlet|meatball|fritter|bhaji|falafel|nugget|popcorn",
    "bakes / pies": r"bake|baked|pie|casserole|gratin|lasagn|quiche|strudel|pirog",
    "tacos / wraps": r"taco|wrap|burrito|quesadilla|tortilla|enchilada",
    "bread / pancakes": r"bread|bun|pancake|crepe|waffle|crumpet|flatbread|tortillas|bagel|roll",
    "sweet / cake": r"cake|cookie|brownie|muffin|pudding|dessert|ice cream|truffle|candy|fudge|tart|smoothie|jam|chocolate",
    "sauces / dips / cheese": r"sauce|dip|hummus|pesto|dressing|cheese|feta|mozzarella|mayo|butter|spread|yogurt",
    "potato / veg sides": r"potato|roasted|mash|hash|fries|wedges|cauliflower|cabbage|kale|chard",
}


def families() -> None:
    recipes = json.loads(RECIPES.read_text())
    groups: dict[str, list[str]] = defaultdict(list)
    for r in recipes:
        title = r["title"].lower()
        fam = next((f for f, pat in FAMILIES.items() if re.search(pat, title)), "other")
        groups[fam].append(r["title"])
    for fam, titles in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{fam} ({len(titles)})")
        for t in titles:
            print(f"   {t[:90]}")


if __name__ == "__main__":
    families() if "--families" in sys.argv else main()
