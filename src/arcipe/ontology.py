"""
Recipe Graph Ontology
=====================
Contains real data
"""

from __future__ import annotations

from arcipe.models import ComponentSlot
from arcipe.models import Ingredient
from arcipe.models import PreparedIngredient
from arcipe.models import Recipe
from arcipe.models import RecipeEdge


# ---------------------------------------------------------------------------
# Ingredient data
# ---------------------------------------------------------------------------

INGREDIENTS: dict[str, Ingredient] = {
    # Leafy greens
    "spinach": Ingredient(
        id="spinach",
        name="Spinach",
        classes=["leafy_green"],
        roles=["sauce_base", "bulk", "garnish"],
        flavour=["earthy", "mild", "fresh"],
        colour="green",
        season=["all"],
        cost="low",
        nutrition=["high_iron", "high_calcium"],
    ),
    "nettle": Ingredient(
        id="nettle",
        name="Nettle",
        classes=["leafy_green"],
        roles=["sauce_base", "bulk"],
        flavour=["earthy", "grassy"],
        colour="green",
        season=["spring", "forage"],
        cost="very_low",
        nutrition=["high_iron"],
        notes="Blanch first to neutralise sting. Remarkable spring ingredient.",
    ),
    "kale": Ingredient(
        id="kale",
        name="Kale",
        classes=["leafy_green", "brassica"],
        roles=["bulk", "sauce_base", "garnish"],
        flavour=["bitter", "earthy"],
        colour="green",
        season=["autumn", "winter"],
        cost="low",
        nutrition=["high_iron", "high_calcium", "high_fibre"],
    ),
    # Root veg
    "potato": Ingredient(
        id="potato",
        name="Potato",
        classes=["root_veg"],
        roles=["bulk", "bulk_blendable"],
        flavour=["earthy", "mild"],
        colour="yellow",
        season=["all"],
        cost="very_low",
        nutrition=["high_carb", "high_fibre"],
    ),
    "carrot": Ingredient(
        id="carrot",
        name="Carrot",
        classes=["root_veg"],
        roles=["bulk", "bulk_blendable", "sweetener"],
        flavour=["sweet", "earthy", "mild"],
        colour="orange",
        season=["all"],
        cost="very_low",
        nutrition=["high_fibre"],
        notes="Roast then blend = extraordinary cheap curry gravy base.",
    ),
    "parsnip": Ingredient(
        id="parsnip",
        name="Parsnip",
        classes=["root_veg"],
        roles=["bulk", "bulk_blendable", "sweetener"],
        flavour=["sweet", "earthy", "mild"],
        colour="white",
        season=["autumn", "winter"],
        cost="very_low",
        nutrition=["high_fibre"],
    ),
    "sweet_potato": Ingredient(
        id="sweet_potato",
        name="Sweet Potato",
        classes=["root_veg"],
        roles=["bulk", "bulk_blendable", "sweetener"],
        flavour=["sweet", "earthy"],
        colour="orange",
        season=["all"],
        cost="low",
        nutrition=["high_carb", "high_fibre"],
    ),
    # Brassica
    "cauliflower": Ingredient(
        id="cauliflower",
        name="Cauliflower",
        classes=["brassica"],
        roles=["bulk", "bulk_blendable"],
        flavour=["mild", "earthy", "nutty"],
        colour="white",
        season=["all"],
        cost="low",
        nutrition=["high_fibre"],
        notes="Roasts beautifully. Takes spice well. Blends into creamy sauce if needed.",
    ),
    # Rhizomes
    "ginger": Ingredient(
        id="ginger",
        name="Fresh Ginger",
        classes=["rhizome"],
        roles=["aromatic", "spice_base"],
        flavour=["spicy", "fresh", "floral"],
        colour="yellow",
        season=["all"],
        cost="low",
    ),
    "turmeric_fresh": Ingredient(
        id="turmeric_fresh",
        name="Fresh Turmeric",
        classes=["rhizome"],
        roles=["aromatic", "spice_base"],
        flavour=["earthy", "spicy"],
        colour="orange",
        season=["all"],
        cost="medium",
    ),
    # Dried spices — treated as always-available pantry, spice_base role
    "garam_masala": Ingredient(
        id="garam_masala",
        name="Garam Masala",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["spicy", "floral", "earthy"],
        colour="brown",
        season=["all"],
        cost="low",
    ),
    "fenugreek_seed": Ingredient(
        id="fenugreek_seed",
        name="Fenugreek Seed",
        classes=["dried_spice"],
        roles=["spice_base", "texture_spice"],
        flavour=["earthy", "bitter", "floral"],
        colour="yellow",
        season=["all"],
        cost="low",
    ),
    "cumin": Ingredient(
        id="cumin",
        name="Cumin",
        classes=["dried_spice"],
        roles=["spice_base", "aromatic"],
        flavour=["earthy", "spicy", "smoky"],
        colour="brown",
        season=["all"],
        cost="low",
    ),
    "coriander_seed": Ingredient(
        id="coriander_seed",
        name="Coriander Seed",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["floral", "earthy", "spicy"],
        colour="brown",
        season=["all"],
        cost="low",
    ),
    "chilli_powder": Ingredient(
        id="chilli_powder",
        name="Chilli Powder",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["spicy"],
        colour="red",
        season=["all"],
        cost="low",
    ),
    "smoked_paprika": Ingredient(
        id="smoked_paprika",
        name="Smoked Paprika",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["smoky", "spicy", "sweet"],
        colour="red",
        season=["all"],
        cost="low",
    ),
    "turmeric_dried": Ingredient(
        id="turmeric_dried",
        name="Turmeric (dried)",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["earthy", "spicy"],
        colour="yellow",
        season=["all"],
        cost="low",
    ),
    "curry_powder": Ingredient(
        id="curry_powder",
        name="Curry Powder",
        classes=["dried_spice"],
        roles=["spice_base"],
        flavour=["spicy", "earthy", "floral"],
        colour="yellow",
        season=["all"],
        cost="low",
        notes="Generic blend. Fine for katsu or mild curries.",
    ),
    "mixed_spice": Ingredient(
        id="mixed_spice",
        name="Mixed Spice",
        classes=["dried_spice"],
        roles=["spice_base", "baking_spice"],
        flavour=["sweet", "spicy", "nutty"],
        colour="brown",
        season=["all"],
        cost="low",
    ),
    "cinnamon_ground": Ingredient(
        id="cinnamon_ground",
        name="Ground Cinnamon",
        classes=["dried_spice"],
        roles=["spice_base", "baking_spice"],
        flavour=["sweet", "spicy", "earthy"],
        colour="brown",
        season=["all"],
        cost="low",
    ),
    # Fats / plant milks
    "coconut_milk": Ingredient(
        id="coconut_milk",
        name="Coconut Milk (full fat)",
        classes=["plant_milk"],
        roles=["fat_emulsifier", "liquid", "sauce_base"],
        flavour=["rich", "sweet", "mild"],
        colour="white",
        season=["all"],
        cost="low",
        nutrition=["high_fat"],
        notes="Full fat only. The lite stuff is a waste of everyone's time.",
    ),
    "butter": Ingredient(
            id="butter",
            name="Butter",
            classes=["fat"],
            roles=["fat_emulsifier"],
            flavour=["rich", "nutty", "rich"],
            colour="yellow",
            season=["all"],
            cost="medium",
            nutrition=["high_fat"],
            notes="May sub for vegan alternative"
            ),
    # Fruit
    "apple": Ingredient(
        id="apple",
        name="Apple",
        classes=["stone_fruit"],
        roles=["sweetener", "bulk_blendable"],
        flavour=["sweet", "sharp"],
        colour="green",
        season=["autumn"],
        cost="low",
        notes="Grated or blended into katsu sauce for natural sweetness.",
    ),
    "banana": Ingredient(
        id="banana",
        name="Banana",
        classes=["stone_fruit"],
        roles=["sweetener", "bulk_blendable"],
        flavour=["sweet"],
        colour="yellow",
        season=["all"],
        cost="very_low",
        notes="Blended into katsu sauce. Sounds wrong, tastes right.",
    ),
    # Legumes
    "peas": Ingredient(
        id="peas",
        name="Peas",
        classes=["legume"],
        roles=["sauce_base", "bulk", "protein", "garnish"],
        flavour=["sweet", "fresh", "grassy"],
        colour="green",
        season=["spring", "summer"],
        cost="low",
        nutrition=["high_protein", "high_fibre"],
        notes="Frozen peas are year-round and excellent. Genuinely polymorphic ingredient.",
    ),
    "chickpeas": Ingredient(
        id="chickpeas",
        name="Chickpeas",
        classes=["legume"],
        roles=["bulk", "protein", "sauce_base"],
        flavour=["nutty", "earthy"],
        colour="yellow",
        season=["all"],
        cost="low",
        nutrition=["high_protein", "high_fibre"],
        notes="Aquafaba (liquid) is separately useful as binder/emulsifier.",
    ),
    "white_beans": Ingredient(
        id="white_beans",
        name="White Beans (cannellini/butter)",
        classes=["legume"],
        roles=["bulk", "protein", "sauce_base", "fat_emulsifier"],
        flavour=["earthy", "rich"],
        colour="white",
        season=["all"],
        cost="low",
        nutrition=["high_protein", "high_fibre"],
        notes="Blended white beans make an incredibly creamy sauce base.",
    ),
    "lentils": Ingredient(
        id="lentils",
        name="Lentils",
        classes=["legume"],
        roles=["bulk", "protein", "sauce_base"],
        flavour=["earthy", "umami"],
        colour="brown",
        season=["all"],
        cost="very_low",
        nutrition=["high_protein", "high_iron", "high_fibre"],
    ),
    "kidney_beans": Ingredient(
        id="kidney_beans",
        name="Kidney Beans",
        classes=["legume"],
        roles=["bulk", "protein"],
        flavour=["earthy", "rich"],
        colour="red",
        season=["all"],
        cost="very_low",
        nutrition=["high_protein", "high_fibre"],
        notes="The canonical rajma bean. Canned is fine, dried is better.",
    ),
    # Grains
    "spaghetti": Ingredient(
        id="spaghetti",
        name="Spaghetti",
        classes=["grain"],
        roles=["grain_base"],
        flavour=["earthy"],
        colour="yellow",
        season=["all"],
        cost="very_low",
        nutrition=["high_carb"],
    ),
    "farro": Ingredient(
        id="farro",
        name="Farro",
        classes=["grain"],
        roles=["grain_base", "bulk"],
        flavour=["nutty", "earthy"],
        colour="brown",
        season=["all"],
        cost="medium",
        nutrition=["high_carb", "high_fibre"],
    ),
    "flatbread": Ingredient(
        id="flatbread",
        name="Flatbread",
        classes=["grain"],
        roles=["grain_base"],
        flavour=["earthy"],
        colour="brown",
        season=["all"],
        cost="low",
        nutrition=["high_carb"],
        notes="Changes the recipe method — bake/grill not boil.",
    ),
    "polenta": Ingredient(
        id="polenta",
        name="Polenta",
        classes=["grain"],
        roles=["grain_base", "bulk"],
        flavour=["sweet", "earthy"],
        colour="yellow",
        season=["all"],
        cost="low",
        nutrition=["high_carb"],
    ),
    # Alliums
    "garlic": Ingredient(
        id="garlic",
        name="Garlic",
        classes=["allium"],
        roles=["aromatic", "umami_boost"],
        flavour=["umami", "spicy"],
        colour="white",
        season=["all"],
        cost="very_low",
    ),
    "onion": Ingredient(
        id="onion",
        name="Onion",
        classes=["allium"],
        roles=["aromatic", "bulk"],
        flavour=["sweet", "umami"],
        colour="white",
        season=["all"],
        cost="very_low",
    ),
    # Nuts and seeds
    "cashews": Ingredient(
        id="cashews",
        name="Cashews",
        classes=["nut"],
        roles=["fat_emulsifier", "sauce_base"],
        flavour=["rich", "sweet", "nutty"],
        colour="white",
        season=["all"],
        cost="medium",
        nutrition=["high_fat"],
        notes="Soaked and blended = exceptional creamy base.",
    ),
    "sunflower_seeds": Ingredient(
        id="sunflower_seeds",
        name="Sunflower Seeds",
        classes=["seed"],
        roles=["fat_emulsifier", "texture_top"],
        flavour=["mild", "nutty"],
        colour="yellow",
        season=["all"],
        cost="low",
        nutrition=["high_fat", "omega3"],
        notes="Soaked and blended = sunflower cream, excellent cheaper cashew alternative.",
    ),
    "pumpkin_seeds": Ingredient(
        id="pumpkin_seeds",
        name="Pumpkin Seeds",
        classes=["seed"],
        roles=["texture_top", "protein", "garnish"],
        flavour=["nutty"],
        colour="green",
        season=["all"],
        cost="low",
        nutrition=["high_protein", "omega3"],
    ),
    "pine_nuts": Ingredient(
        id="pine_nuts",
        name="Pine Nuts",
        classes=["nut"],
        roles=["texture_top", "fat_emulsifier"],
        flavour=["rich", "nutty"],
        colour="white",
        season=["all"],
        cost="high",
    ),
    # Acids
    "lemon": Ingredient(
        id="lemon",
        name="Lemon",
        classes=["citrus"],
        roles=["acid_bright", "garnish"],
        flavour=["sharp", "floral"],
        colour="yellow",
        season=["all"],
        cost="low",
    ),
    "capers": Ingredient(
        id="capers",
        name="Capers",
        classes=["fermented"],
        roles=["acid_bright", "umami_boost", "texture_top", "garnish"],
        flavour=["sharp", "umami"],
        colour="green",
        season=["all"],
        cost="medium",
        notes="Fried capers are a texture revelation.",
    ),
    # Vinegar
    "white_wine_vinegar": Ingredient(
        id="white_wine_vinegar",
        name="White Wine Vinegar",
        classes=["vinegar"],
        roles=["acid_component", "acid_bright"],
        flavour=["sharp"],
        colour="white",
        season=["all"],
        cost="low",
        notes="Structural acid in vindaloo — required, not optional.",
    ),
    "apple_cider_vinegar": Ingredient(
        id="apple_cider_vinegar",
        name="Apple Cider Vinegar",
        classes=["vinegar"],
        roles=["acid_component", "acid_bright"],
        flavour=["sharp", "sweet"],
        colour="yellow",
        season=["all"],
        cost="low",
    ),
    # Umami boosters
    "miso": Ingredient(
        id="miso",
        name="White Miso",
        classes=["fermented"],
        roles=["umami_boost", "fat_emulsifier"],
        flavour=["umami", "rich"],
        colour="yellow",
        season=["all"],
        cost="medium",
        nutrition=["b12_source"],
    ),
    "nutritional_yeast": Ingredient(
        id="nutritional_yeast",
        name="Nutritional Yeast",
        classes=["fermented"],
        roles=["umami_boost"],
        flavour=["umami", "nutty"],
        colour="yellow",
        season=["all"],
        cost="medium",
        nutrition=["b12_source"],
    ),
    # Fats
    "olive_oil": Ingredient(
        id="olive_oil",
        name="Olive Oil",
        classes=["fat"],
        roles=["fat_emulsifier", "aromatic"],
        flavour=["rich", "earthy"],
        colour="yellow",
        season=["all"],
        cost="medium",
        nutrition=["high_fat"],
    ),
    # Fungus
    "mushrooms": Ingredient(
        id="mushrooms",
        name="Mushrooms",
        classes=["fungus"],
        roles=["bulk", "umami_boost"],
        flavour=["umami", "earthy"],
        colour="brown",
        season=["autumn", "winter"],
        cost="low",
        nutrition=["high_fibre"],
    ),
    # Nightshade
    "tomato": Ingredient(
        id="tomato",
        name="Tomato",
        classes=["nightshade"],
        roles=["sauce_base", "bulk", "acid_bright"],
        flavour=["umami", "sweet", "sharp"],
        colour="red",
        season=["summer"],
        cost="low",
        nutrition=["high_fibre"],
    ),
    # Protein base
    "tofu": Ingredient(
        id="tofu",
        name="Tofu (firm)",
        classes=["soy_product"],
        roles=["bulk", "protein"],
        flavour=["mild"],
        colour="white",
        season=["all"],
        cost="low",
        nutrition=["high_protein", "high_calcium"],
        notes="Press well before using. Takes on sauce flavour readily.",
    ),
    # Thickener
    "plain_flour": Ingredient(
        id="plain_flour",
        name="Plain Flour",
        classes=["grain"],
        roles=["thickener", "binder"],
        flavour=["mild"],
        colour="white",
        season=["all"],
        cost="very_low",
    ),
    "tomato_puree": Ingredient(
        id="tomato_puree",
        name="Tomato Puree",
        classes=["nightshade"],
        roles=["thickener", "umami_boost"],
        flavour=["rich", "umami"],
        colour="red",
        season=["all"],
        cost="low",
    ),
    "tomato_ketchup": Ingredient(
        id="tomato_ketchup",
        name="Tomato Ketchup",
        classes=["nightshade"],
        roles=["thickener", "umami_boost", "sweetener"],
        flavour=["rich", "umami", "sweet"],
        colour="red",
        season=["all"],
        cost="low",
    ),
    # Herbs
    "coriander_leaf": Ingredient(
        id="coriander_leaf",
        name="Fresh Coriander",
        classes=["fresh_herb"],
        roles=["garnish", "finish_herb"],
        flavour=["fresh", "floral"],
        colour="green",
        season=["all"],
        cost="low",
    ),
    # Canned tomato — separate from fresh
    "canned_tomato": Ingredient(
        id="canned_tomato",
        name="Canned Tomatoes",
        classes=["nightshade"],
        roles=["sauce_base", "liquid", "acid_bright"],
        flavour=["umami", "sweet", "sharp"],
        colour="red",
        season=["all"],
        cost="very_low",
        nutrition=["high_fibre"],
        notes="Year-round pantry staple. Often better than out-of-season fresh.",
    ),
    # Rice
    "basmati_rice": Ingredient(
        id="basmati_rice",
        name="Basmati Rice",
        classes=["grain"],
        roles=["grain_base"],
        flavour=["earthy", "floral"],
        colour="white",
        season=["all"],
        cost="very_low",
        nutrition=["high_carb"],
    ),
    "brown_rice": Ingredient(
        id="brown_rice",
        name="Brown Rice",
        classes=["grain"],
        roles=["grain_base"],
        flavour=["earthy"],
        colour="brown",
        season=["all"],
        cost="very_low",
        nutrition=["high_carb"],
    ),
    "sushi_rice": Ingredient(
        id="sushi_rice",
        name="Sushi Rice",
        classes=["grain"],
        roles=["grain_base"],
        flavour=["earthy", "floral"],
        colour="white",
        season=["all"],
        cost="low",
        nutrition=["high_carb"],
    ),
}


# ---------------------------------------------------------------------------
# Prepared ingredients — derived from base ingredients + method
# ---------------------------------------------------------------------------

PREPARED_INGREDIENTS: dict[str, PreparedIngredient] = {
    "sunflower_cream": PreparedIngredient(
        id="sunflower_cream",
        base_ingredient="sunflower_seeds",
        method="blend",
        unlocked_roles=["fat_emulsifier", "sauce_base"],
        notes="Soak 4hrs, drain, blend with water. Cheaper cashew cream substitute.",
    ),
    "cashew_cream": PreparedIngredient(
        id="cashew_cream",
        base_ingredient="cashews",
        method="blend",
        unlocked_roles=["fat_emulsifier", "sauce_base"],
        notes="Soak 2hrs, drain, blend with water until very smooth.",
    ),
    "roasted_carrot": PreparedIngredient(
        id="roasted_carrot",
        base_ingredient="carrot",
        method="roast",
        unlocked_roles=["sauce_base", "bulk_blendable"],
        notes="Roast at 200°C until caramelised. Blends into sweet gravy base.",
    ),
    "roux": PreparedIngredient(
        id="roux",
        base_ingredient="plain_flour",
        method="fry",
        unlocked_roles=["thickener", "sauce_base"],
        notes="Equal parts flour and vegan butter/oil, cooked out 2 mins.",
    ),
}

# ---------------------------------------------------------------------------
# Reusable component slot definitions
# ---------------------------------------------------------------------------

SLOTS: dict[str, ComponentSlot] = {
    "grain_base": ComponentSlot(
        id="grain_base",
        label="Grain Base",
        required_roles=["grain_base"],
        preferred_classes=["grain"],
        required_method=["boil", "bake", "steam"],
        max_ingredients=1,
    ),
    "green_sauce": ComponentSlot(
        id="green_sauce",
        label="Green Sauce Base",
        required_roles=["sauce_base"],
        preferred_classes=["leafy_green", "legume"],
        required_method=["blend"],
        max_ingredients=2,
        notes="Primary body of the sauce. Should be green. Blend with liquid.",
    ),
    "creamy_sauce": ComponentSlot(
        id="creamy_sauce",
        label="Creamy Sauce Base",
        required_roles=["sauce_base", "fat_emulsifier"],
        preferred_classes=["legume", "nut"],
        required_method=["blend"],
        max_ingredients=2,
    ),
    "fat_emulsifier": ComponentSlot(
        id="fat_emulsifier",
        label="Fat / Emulsifier",
        required_roles=["fat_emulsifier"],
        preferred_classes=["nut", "fat"],
        required_method=["raw", "blend", "soak"],
        max_ingredients=1,
    ),
    "aromatic_base": ComponentSlot(
        id="aromatic_base",
        label="Aromatic Base",
        required_roles=["aromatic"],
        preferred_classes=["allium"],
        required_method=["fry"],
        max_ingredients=2,
    ),
    "protein_bulk": ComponentSlot(
        id="protein_bulk",
        label="Protein / Bulk",
        required_roles=["protein"],
        preferred_classes=["legume"],
        required_method=["boil", "roast", "raw"],
        max_ingredients=2,
    ),
    "acid_bright": ComponentSlot(
        id="acid_bright",
        label="Acid / Brightness",
        required_roles=["acid_bright"],
        preferred_classes=["citrus", "fermented"],
        required_method=["raw"],
        optional=True,
        max_ingredients=1,
    ),
    "umami_boost": ComponentSlot(
        id="umami_boost",
        label="Umami Boost",
        required_roles=["umami_boost"],
        preferred_classes=["fermented"],
        required_method=["raw"],
        optional=True,
        max_ingredients=1,
    ),
    "texture_top": ComponentSlot(
        id="texture_top",
        label="Texture Top",
        required_roles=["texture_top"],
        preferred_classes=["seed", "nut", "fermented"],
        required_method=["toast", "fry", "raw"],
        optional=True,
        max_ingredients=1,
    ),
    "curry_aromatic": ComponentSlot(
        id="curry_aromatic",
        label="Curry Aromatic Base",
        required_roles=["aromatic"],
        preferred_classes=["allium", "rhizome"],
        required_method=["fry"],
        max_ingredients=3,
        notes="Onion + garlic + ginger is the classic trinity.",
    ),
    "spice_bloom": ComponentSlot(
        id="spice_bloom",
        label="Spice Bloom",
        required_roles=["spice_base"],
        preferred_classes=["dried_spice", "rhizome"],
        required_method=["fry", "raw"],
        max_ingredients=4,
        notes="Dry spices hit the hot oil after aromatics. Always-available pantry slot.",
    ),
    "whole_spice_bloom": ComponentSlot(
        id="whole_spice_bloom",
        label="Whole Spice Bloom",
        required_roles=["texture_spice"],
        preferred_classes=["dried_spice"],
        required_method=["fry", "toast", "roast"],
        max_ingredients=3,
        notes="Whole spices for texture, aromatic and flavour.",
    ),
    "tomato_sauce_body": ComponentSlot(
        id="tomato_sauce_body",
        label="Tomato Sauce Body",
        required_roles=["sauce_base"],
        preferred_classes=["nightshade"],
        required_method=["boil", "blend", "fry"],
        max_ingredients=1,
        notes="Fresh preferred, canned always acceptable.",
    ),
    "tomato_enhancement": ComponentSlot(
        id="tomato_enhancement",
        label="Tomato Flavour Enhancement",
        required_roles=["flavour_enhancer", "thickener"],
        required_method=["fry", "raw"],
        max_ingredients=1,
        notes="Add tomato puree (or ketchup!) to dish - fry with spices or add to sauce",
    ),
    "creamy_curry_fat": ComponentSlot(
        id="creamy_curry_fat",
        label="Creamy Fat",
        required_roles=["fat_emulsifier"],
        preferred_classes=["plant_milk", "nut", "seed"],
        required_method=["raw", "blend"],
        max_ingredients=1,
        notes="Coconut milk, cashew cream, or sunflower cream.",
    ),
    "curry_protein": ComponentSlot(
        id="curry_protein",
        label="Curry Protein / Bulk",
        required_roles=["protein"],
        preferred_classes=["legume", "brassica", "soy_product"],
        required_method=["boil", "roast", "raw", "fry"],
        max_ingredients=2,
    ),
    "curry_bulk": ComponentSlot(
        id="curry_bulk",
        label="Curry Bulk Vegetables",
        required_roles=["bulk"],
        preferred_classes=["leafy_green", "squash", "fruit_veg"],
        required_method=["fry", "roast", "raw"],
        max_ingredients=2,
    ),
    "starchy_bulk": ComponentSlot(
        id="starchy_bulk",
        label="Starchy Bulk",
        required_roles=["bulk"],
        preferred_classes=["root_veg"],
        required_method=["boil", "roast"],
        optional=True,
        max_ingredients=2,
        notes="Potato in saag aloo, sweet potato elsewhere.",
    ),
    "acid_component": ComponentSlot(
        id="acid_component",
        label="Structural Acid",
        required_roles=["acid_component"],
        preferred_classes=["vinegar"],
        required_method=["raw"],
        optional=False,  # required in vindaloo
        max_ingredients=1,
        notes="Vindaloo requires this. Not optional here.",
    ),
    "root_sauce_body": ComponentSlot(
        id="root_sauce_body",
        label="Root Veg Sauce Body",
        required_roles=["bulk_blendable"],
        preferred_classes=["root_veg"],
        required_method=["roast", "blend"],
        max_ingredients=2,
        notes="Roast until caramelised, blend with stock. Carrot is canonical.",
    ),
    "katsu_sauce_body": ComponentSlot(
        id="katsu_sauce_body",
        label="Katsu Sauce Body",
        required_roles=["thickener"],
        preferred_classes=["grain"],
        required_method=["fry"],
        max_ingredients=1,
        notes="Roux base. Flour cooked in fat.",
    ),
    "katsu_sweetener": ComponentSlot(
        id="katsu_sweetener",
        label="Katsu Sweetener",
        required_roles=["sweetener"],
        preferred_classes=["stone_fruit"],
        required_method=["blend", "raw"],
        max_ingredients=2,
        notes="Apple + banana blended in. The signature katsu sweetness.",
    ),
    "finish_herb": ComponentSlot(
        id="finish_herb",
        label="Finish Herb",
        required_roles=["finish_herb"],
        preferred_classes=["fresh_herb"],
        required_method=["raw"],
        optional=True,
        max_ingredients=1,
    ),
    "curry_grain_base": ComponentSlot(
        id="curry_grain_base",
        label="Curry Grain Base",
        required_roles=["grain_base"],
        preferred_classes=["grain"],
        required_method=["boil", "steam"],
        max_ingredients=1,
        notes="Rice preferred for curries. Flatbread works too.",
    ),
    "saag_green": ComponentSlot(
        id="saag_green",
        label="Saag Green Base",
        required_roles=["sauce_base"],
        preferred_classes=["leafy_green"],
        required_method=["blend", "wilt"],
        max_ingredients=2,
        notes="Frozen spinach is canonical and excellent here.",
    ),
}


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

RECIPES: dict[str, Recipe] = {
    # ------------------------------------------------------------------
    # Green Pasta
    # ------------------------------------------------------------------
    "green_pasta": Recipe(
        id="green_pasta",
        name="Green Pasta",
        description=(
            "Silky blended green sauce coating long pasta. "
            "Sauce base can be peas, spinach, nettle, or any blendable green. "
            "Fat emulsifier (cashews, white beans) gives body."
        ),
        colour_dominant="green",
        slots=[
            SLOTS["grain_base"],
            SLOTS["green_sauce"],
            SLOTS["fat_emulsifier"],
            SLOTS["aromatic_base"],
            SLOTS["acid_bright"],
            SLOTS["umami_boost"],
            SLOTS["texture_top"],
        ],
        edges=[
            RecipeEdge(
                from_slot="aromatic_base", to_slot="green_sauce", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="fat_emulsifier", to_slot="green_sauce", edge_type="emulsify"
            ),
            RecipeEdge(
                from_slot="umami_boost", to_slot="green_sauce", edge_type="fold_in"
            ),
            RecipeEdge(from_slot="green_sauce", to_slot="grain_base", edge_type="coat"),
            RecipeEdge(
                from_slot="acid_bright", to_slot="grain_base", edge_type="finish"
            ),
            RecipeEdge(from_slot="texture_top", to_slot="grain_base", edge_type="top"),
        ],
        notes="spaghetti + peas + cashews + garlic + lemon + nooch + pine nuts is the canonical fill.",
    ),
    # ------------------------------------------------------------------
    # Green Grain Bowl
    # ------------------------------------------------------------------
    "green_grain_bowl": Recipe(
        id="green_grain_bowl",
        name="Green Grain Bowl",
        description=(
            "Hearty grain base with a punchy blended green sauce, "
            "substantial protein/bulk, acid, and a seedy top. "
            "More rustic than the pasta — sauce is more of a dressing than a coating."
        ),
        colour_dominant="green",
        slots=[
            SLOTS["grain_base"],
            SLOTS["green_sauce"],
            SLOTS["protein_bulk"],
            SLOTS["aromatic_base"],
            SLOTS["acid_bright"],
            SLOTS["texture_top"],
        ],
        edges=[
            RecipeEdge(
                from_slot="aromatic_base", to_slot="green_sauce", edge_type="combine"
            ),
            RecipeEdge(from_slot="green_sauce", to_slot="grain_base", edge_type="coat"),
            RecipeEdge(
                from_slot="protein_bulk", to_slot="grain_base", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="acid_bright", to_slot="grain_base", edge_type="finish"
            ),
            RecipeEdge(from_slot="texture_top", to_slot="grain_base", edge_type="top"),
        ],
        notes=(
            "farro + nettle pesto + white beans + garlic + lemon + pumpkin seeds. "
            "Or: polenta + kale sauce + chickpeas + capers + pumpkin seeds."
        ),
    ),
    # ------------------------------------------------------------------
    # Green Flatbread
    # ------------------------------------------------------------------
    "green_flatbread": Recipe(
        id="green_flatbread",
        name="Green Flatbread",
        description=(
            "Flatbread as the base. Green sauce is spread not coated. "
            "Bulk veg goes on top rather than folded in. "
            "Different texture register — crispy base, soft spread, chunky top."
        ),
        colour_dominant="green",
        slots=[
            SLOTS["grain_base"],
            SLOTS["green_sauce"],
            SLOTS["protein_bulk"],
            SLOTS["fat_emulsifier"],
            SLOTS["acid_bright"],
            SLOTS["texture_top"],
        ],
        edges=[
            RecipeEdge(
                from_slot="fat_emulsifier", to_slot="green_sauce", edge_type="emulsify"
            ),
            RecipeEdge(from_slot="green_sauce", to_slot="grain_base", edge_type="coat"),
            RecipeEdge(from_slot="protein_bulk", to_slot="grain_base", edge_type="top"),
            RecipeEdge(
                from_slot="acid_bright", to_slot="grain_base", edge_type="finish"
            ),
            RecipeEdge(from_slot="texture_top", to_slot="grain_base", edge_type="top"),
        ],
        notes=(
            "flatbread + pea & basil spread + chickpeas + olive oil + capers + fried capers. "
            "Grill the flatbread for char."
        ),
    ),
    # ------------------------------------------------------------------
    # Creamy Tomato Lentil (contrasting recipe — not green)
    # ------------------------------------------------------------------
    "creamy_tomato_lentil": Recipe(
        id="creamy_tomato_lentil",
        name="Creamy Tomato Lentil",
        description=(
            "A rich tomato-based sauce with lentils as the protein. "
            "Cashews or white beans as fat emulsifier give it creaminess without dairy. "
            "Demonstrates same component structure with completely different ingredient fills."
        ),
        colour_dominant="red",
        slots=[
            SLOTS["grain_base"],
            SLOTS["creamy_sauce"],
            SLOTS["protein_bulk"],
            SLOTS["aromatic_base"],
            SLOTS["umami_boost"],
            SLOTS["acid_bright"],
            SLOTS["texture_top"],
        ],
        edges=[
            RecipeEdge(
                from_slot="aromatic_base", to_slot="creamy_sauce", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="umami_boost", to_slot="creamy_sauce", edge_type="fold_in"
            ),
            RecipeEdge(
                from_slot="protein_bulk", to_slot="creamy_sauce", edge_type="fold_in"
            ),
            RecipeEdge(
                from_slot="creamy_sauce", to_slot="grain_base", edge_type="coat"
            ),
            RecipeEdge(
                from_slot="acid_bright", to_slot="creamy_sauce", edge_type="finish"
            ),
            RecipeEdge(from_slot="texture_top", to_slot="grain_base", edge_type="top"),
        ],
        notes=(
            "spaghetti + tomato & cashew sauce + lentils + onion & garlic + miso + lemon + "
            "toasted pumpkin seeds. Miso is the secret — rounds out the tomato."
        ),
    ),
    # ------------------------------------------------------------------
    # Rajma — kidney bean curry, light tomato base
    # ------------------------------------------------------------------
    "rajma": Recipe(
        id="rajma",
        name="Rajma",
        description=(
            "Simple, light kidney bean curry. Tomato forward, no cream. "
            "Spiced but not fiery. One of those dishes that's better the next day."
        ),
        colour_dominant="red",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["tomato_sauce_body"],
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="spice_bloom",
                to_slot="tomato_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="curry_protein",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="tomato_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        preferred_ingredients=[
            "tomato",
            "cumin",
            "coriander_seed",
            "turmeric_dried",
            "kidney_beans",
        ],
        notes=(
            "kidney_beans strongly preferred for curry_protein — "
            "could add preferred_ingredient field later. "
            "Fresh tomato preferred, canned fine."
        ),
    ),
    # ------------------------------------------------------------------
    # Butter Chickpeas — creamy tomato, coconut/cashew fat
    # ------------------------------------------------------------------
    "butter_chickpeas": Recipe(
        id="butter_chickpeas",
        name="Butter Chickpeas",
        description=(
            "Vegan butter chicken adjacent. Rich creamy tomato sauce, "
            "chickpeas as protein. Fat emulsifier is coconut milk or cashew/sunflower cream. "
            "Garam masala and cumin forward."
        ),
        colour_dominant="orange",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["tomato_sauce_body"],
            SLOTS["creamy_curry_fat"],
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="spice_bloom",
                to_slot="tomato_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="creamy_curry_fat",
                to_slot="tomato_sauce_body",
                edge_type="emulsify",
            ),
            RecipeEdge(
                from_slot="curry_protein",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="tomato_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        preferred_ingredients=[
            "garam_masala",
            "turmeric_dried",
            "smoked_paprika",
            "mixed_spice",
            "canned_tomato",
        ],
        notes="chickpeas strongly preferred. Blitz half the sauce before adding chickpeas for texture.",
    ),
    # ------------------------------------------------------------------
    # Cauliflower Madras — spicy tomato, no cream
    # ------------------------------------------------------------------
    "cauliflower_madras": Recipe(
        id="cauliflower_madras",
        name="Cauliflower Madras",
        description=(
            "Hot, dry-ish tomato curry. Cauliflower roasted before going in — "
            "keeps texture. Chilli forward, no fat emulsifier."
        ),
        colour_dominant="red",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["tomato_sauce_body"],
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="spice_bloom",
                to_slot="tomato_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="curry_protein",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="tomato_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        preferred_ingredients=[
            "garam_masala",
            "chilli_powder",
            "turmeric_dried",
            "cinnamon",
            "cauliflower",
            "ginger",
            "canned_tomato",
        ],
        notes=(
            "cauliflower preferred for curry_protein — roast at 200°C first. "
            "Same skeleton as rajma, spice profile is what differentiates."
        ),
    ),
    # ------------------------------------------------------------------
    # Vindaloo — tomato base, structural vinegar required
    # ------------------------------------------------------------------
    "vindaloo": Recipe(
        id="vindaloo",
        name="Vindaloo",
        description=(
            "Fierce. Tomato and vinegar base, heavy chilli. "
            "Vinegar is structural not optional — it's what makes it vindaloo. "
            "Chickpeas or potato as bulk."
        ),
        colour_dominant="red",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["tomato_sauce_body"],
            SLOTS["acid_component"],  # required — not optional
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="spice_bloom",
                to_slot="tomato_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="acid_component",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="curry_protein",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="tomato_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        notes="acid_component required — without vinegar it's just a hot tomato curry, not a vindaloo.",
    ),
    # ------------------------------------------------------------------
    # Saag — green sauce, starchy bulk
    # ------------------------------------------------------------------
    "saag": Recipe(
        id="saag",
        name="Saag (Aloo / Tofu / Chana)",
        description=(
            "Spinach-based green curry sauce. Starchy bulk slot takes potato (aloo), "
            "tofu, or chickpeas (chana). Protein slot covers chickpeas/tofu. "
            "Creamy fat optional but recommended."
        ),
        colour_dominant="green",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["whole_spice_bloom"],
            SLOTS["spice_bloom"],
            SLOTS["saag_green"],
            SLOTS["creamy_curry_fat"],
            SLOTS["starchy_bulk"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="whole_spice_bloom",
                to_slot="curry_aromatic",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="spice_bloom", to_slot="saag_green", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="creamy_curry_fat", to_slot="saag_green", edge_type="emulsify"
            ),
            RecipeEdge(
                from_slot="starchy_bulk", to_slot="saag_green", edge_type="fold_in"
            ),
            RecipeEdge(
                from_slot="saag_green", to_slot="curry_grain_base", edge_type="coat"
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        preferred_ingredients=[
            "cumin",
            "coriander_seed",
            "turmeric_dried",
            "fenugreek_seed",
        ],
        notes="Frozen spinach is canonical. Fresh works but you need a lot of it.",
    ),
    # ------------------------------------------------------------------
    # Katsu Curry
    # ------------------------------------------------------------------
    "katsu_curry": Recipe(
        id="katsu_curry",
        name="Katsu Curry",
        description=(
            "Japanese-style mild sweet curry sauce. Roux base thickened with flour, "
            "sweetened with apple and banana, spiced with curry powder. "
            "Serve over rice with whatever bulk veg you fancy — aubergine, tofu, cauliflower."
        ),
        colour_dominant="yellow",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["tomato_enhancement"],
            SLOTS["katsu_sauce_body"],
            SLOTS["katsu_sweetener"],
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic",
                to_slot="katsu_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="spice_bloom", to_slot="katsu_sauce_body", edge_type="fold_in"
            ),
            RecipeEdge(
                from_slot="katsu_sweetener",
                to_slot="katsu_sauce_body",
                edge_type="blend",
            ),
            RecipeEdge(
                from_slot="curry_protein", to_slot="katsu_sauce_body", edge_type="top"
            ),
            RecipeEdge(
                from_slot="tomato_enhancement",
                to_slot="katsu_sauce_body",
                edge_type="combine",
            ),
            RecipeEdge(
                from_slot="katsu_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        preferred_ingredients=[
            "plain_flour",
            # "butter", # Not an ingredient YET
            "tomato_ketchup",
            "tofu",
            "curry_powder",
            "garam_masala",
        ],
        notes="plain_flour fills katsu_sauce_body. Cauliflower or tofu as curry_protein. Ketchup used over puree.",
    ),
    # ------------------------------------------------------------------
    # Roasted Carrot Curry Gravy
    # ------------------------------------------------------------------
    "carrot_curry_gravy": Recipe(
        id="carrot_curry_gravy",
        name="Roasted Carrot Curry Gravy",
        description=(
            "Cheap, deeply satisfying. Root veg roasted until caramelised then blended "
            "with tomato and spices into a smooth, mildly sweet gravy. "
            "Carrot is canonical but parsnip, squash, sweet potato all work. "
            "Protein slot optional — works as a side gravy or a main with legumes."
        ),
        colour_dominant="orange",
        slots=[
            SLOTS["curry_grain_base"],
            SLOTS["curry_aromatic"],
            SLOTS["spice_bloom"],
            SLOTS["root_sauce_body"],
            SLOTS["tomato_sauce_body"],
            SLOTS["curry_protein"],
            SLOTS["finish_herb"],
        ],
        edges=[
            RecipeEdge(
                from_slot="curry_aromatic", to_slot="spice_bloom", edge_type="combine"
            ),
            RecipeEdge(
                from_slot="root_sauce_body",
                to_slot="tomato_sauce_body",
                edge_type="blend",
            ),
            RecipeEdge(
                from_slot="spice_bloom",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="curry_protein",
                to_slot="tomato_sauce_body",
                edge_type="fold_in",
            ),
            RecipeEdge(
                from_slot="tomato_sauce_body",
                to_slot="curry_grain_base",
                edge_type="coat",
            ),
            RecipeEdge(
                from_slot="finish_herb", to_slot="curry_grain_base", edge_type="finish"
            ),
        ],
        notes=(
            "carrot strongly preferred for root_sauce_body — cheap as chips. "
            "Roast at 200°C with a little oil until edges are dark. "
            "Blend with stock before combining with tomato."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Quick sanity check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Ingredients: {len(INGREDIENTS)}")
    print(f"Recipes:     {len(RECIPES)}\n")

    for recipe in RECIPES.values():
        print(f"[{recipe.id}] {recipe.name}")
        print(f"  {recipe.description}")
        print(f"  Slots: {[s.id for s in recipe.slots]}")
        print(f"  Edges: {len(recipe.edges)}")
        print()
