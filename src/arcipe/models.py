"""
Recipe Graph data models
========================
Abstract ingredient/component/recipe models for constraint-based meal planning.
"""

from dataclasses import field
from typing import Literal

from pydantic import BaseModel
from pydantic import Field


# ---------------------------------------------------------------------------
# Literals — the controlled vocabulary
# ---------------------------------------------------------------------------

# What job an ingredient can do in a dish
Role = Literal[
    "grain_base",  # the starchy base you eat everything on/with
    "sauce_base",  # primary body of a sauce when blended/cooked down
    "bulk",  # fills the dish out, present as chunks or pieces
    "protein",  # primary protein contribution
    "fat_emulsifier",  # adds richness, helps sauce bind
    "acid_bright",  # sharpness/lift (lemon, vinegar, capers)
    "umami_boost",  # depth, savouriness (miso, tomato, soy, nutritional yeast)
    "aromatic",  # base flavour layer, usually cooked (onion, garlic, celery)
    "texture_top",  # crunch or contrast on top (seeds, breadcrumbs, fried things)
    "garnish",  # fresh finish, visual, optional
    "binder",  # holds things together (flax egg, aquafaba, starch)
    "sweetener",  # natural sweetness (dates, maple, fruit)
    "liquid",  # stock, water, plant milk — carries flavour
    "texture_spice", # whole spices
    "spice_base",  # any spices
    "bulk_blendable",  # carrots, lentils, things that bulk sauces
    "acid_component",  # vinegar, lemon, for example in vindaloo
    "thickener",  # flour, etc
    "finish_herb",  # fresh coriander, basil, etc
    "baking_spice", #mixed spice, etc
    "flavour_enhancer", # Tomato puree, msg, soy sauce, etc
]

# Broad flavour contribution
FlavourNote = Literal[
    "sweet",
    "bitter",
    "earthy",
    "fresh",
    "grassy",
    "mild",
    "rich",
    "sharp",
    "nutty",
    "smoky",
    "umami",
    "floral",
    "spicy",
]

# Cooking methods that transform an ingredient into a prepared form
Method = Literal[
    "raw",
    "blend",
    "roast",
    "boil",
    "steam",
    "wilt",
    "fry",
    "toast",
    "pickle",
    "ferment",
    "soak",
    "bake",
]

# Broad ingredient class — the top-level ontology
IngredientClassName = Literal[
    "leafy_green",
    "brassica",
    "allium",
    "legume",
    "grain",
    "root_veg",
    "squash",
    "fungus",
    "nightshade",
    "fruit_veg",  # courgette, pepper, cucumber — veg by use, fruit by botany
    "citrus",
    "nut",
    "seed",
    "fresh_herb",
    "dried_spice",
    "fermented",
    "fat",  # oils, nut butters
    "plant_milk",
    "sea_veg",
    "rhizome",
    "soy_product",
    "stone_fruit",
    "vinegar",
]

# Nutritional flags (soft constraints for meal planner)
NutritionFlag = Literal[
    "high_protein",
    "high_iron",
    "high_calcium",
    "high_fibre",
    "b12_source",
    "omega3",
    "high_carb",
    "high_fat",
]

Season = Literal["spring", "summer", "autumn", "winter", "all", "forage"]

CostTier = Literal["very_low", "low", "medium", "high"]

# Colour — used for harmony constraints in meal planner
ColourFamily = Literal[
    "green", "red", "orange", "yellow", "white", "brown", "purple", "black"
]

COLOUR_RGB: dict[str, tuple[int, int, int]] = {
    "green":  (80,  140, 80),
    "red":    (200, 60,  60),
    "orange": (220, 130, 50),
    "yellow": (220, 200, 80),
    "white":  (240, 240, 240),
    "brown":  (140, 90,  50),
    "purple": (130, 70,  160),
    "black":  (30,  30,  30),
}

def colour_distance(a: str, b: str) -> float:
    ra, ga, ba = COLOUR_RGB[a]
    rb, gb, bb = COLOUR_RGB[b]
    return ((ra-rb)**2 + (ga-gb)**2 + (ba-bb)**2) ** 0.5

# Max possible distance (black to white) ≈ 416
COLOUR_MAX = 416.0

# How components connect to each other in a recipe DAG
EdgeType = Literal[
    "combine",  # mix together
    "coat",  # sauce coats base
    "fold_in",  # gently incorporate
    "top",  # place on top of
    "finish",  # add at the end, raw
    "deglaze",  # liquid hits hot pan
    "emulsify",  # blend into smooth sauce
    "blend",  # same
]


# ---------------------------------------------------------------------------
# Ingredient Class — top-level ontology node
# ---------------------------------------------------------------------------


class IngredientClass(BaseModel):
    id: str
    name: str
    typical_roles: list[Role]
    typical_flavour: list[FlavourNote]
    typical_colour: ColourFamily
    notes: str = ""


# ---------------------------------------------------------------------------
# Ingredient — concrete ingredient, member of one or more classes
# ---------------------------------------------------------------------------


class Ingredient(BaseModel):
    id: str
    name: str
    classes: list[IngredientClassName]
    # Roles this ingredient can play — may extend or restrict class defaults
    roles: list[Role]
    flavour: list[FlavourNote]
    colour: ColourFamily
    season: list[Season]
    cost: CostTier
    nutrition: list[NutritionFlag] = Field(default_factory=list)
    perishable: bool = False
    unlimited: bool = False # Is a cupboard staple I won't run out of, e.g. flour, nuts, spices
    notes: str = ""


# ---------------------------------------------------------------------------
# PreparedIngredient — ingredient + method = something usable in a component
# ---------------------------------------------------------------------------


class PreparedIngredient(BaseModel):
    id: str  # e.g. "spinach__wilted"
    base_ingredient: str  # ref to Ingredient.id
    method: Method
    unlocked_roles: list[Role]  # what roles this preparation enables
    notes: str = ""


# ---------------------------------------------------------------------------
# ComponentSlot — abstract slot in a recipe, filled by matching ingredients
# ---------------------------------------------------------------------------


class ComponentSlot(BaseModel):
    id: str
    label: str  # human readable e.g. "Green Sauce Base"
    required_roles: list[Role]  # ingredient must cover ALL of these
    preferred_roles: list[Role] = Field(
        default_factory=list
    )  # Bonus if role covers these additional roles
    preferred_classes: list[IngredientClassName] = Field(default_factory=list)
    required_method: list[Method]  # acceptable prep methods
    optional: bool = False  # if True, slot can be left empty
    max_ingredients: int = 3  # prevent slot becoming a dumping ground
    required_colour: ColourFamily | None = None
    colour_tolerance: float = 0.4   # 0.0 = exact, 1.0 = anything goes
    notes: str = ""


# ---------------------------------------------------------------------------
# RecipeEdge — directed connection between two component slots
# ---------------------------------------------------------------------------


class RecipeEdge(BaseModel):
    from_slot: str  # ComponentSlot.id
    to_slot: str  # ComponentSlot.id
    edge_type: EdgeType


# ---------------------------------------------------------------------------
# Recipe — a DAG of component slots
# ---------------------------------------------------------------------------


class Recipe(BaseModel):
    id: str
    name: str
    description: str
    slots: list[ComponentSlot]
    edges: list[RecipeEdge]
    preferred_ingredients: list[str] = field(default_factory=list)
    # Hard constraints
    no_ingredient_reuse: bool = True  # same ingredient can't fill two slots
    colour_dominant: ColourFamily | None = None
    serves: int = 2
    notes: str = ""
