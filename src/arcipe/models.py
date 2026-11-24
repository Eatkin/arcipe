"""
Recipe Graph data models
========================
Abstract ingredient/component/recipe models for constraint-based meal planning.
"""

from enum import IntEnum
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import BeforeValidator
from pydantic import ConfigDict
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
    "texture_spice",  # whole spices
    "spice_base",  # any spices
    "bulk_blendable",  # carrots, lentils, things that bulk sauces
    "acid_component",  # vinegar, lemon, for example in vindaloo
    "thickener",  # flour, etc
    "finish_herb",  # fresh coriander, basil, etc
    "baking_spice",  # mixed spice, etc
    "flavour_enhancer",  # Tomato puree, msg, soy sauce, etc
    "gluten",  # Bread, cake etc
    "raising_agent",
    "basic",  # as in, not acidic
    "meltable",
    "heat",  # chilli in any form: fresh, flakes, hot sauce
    "dried_herb",  # oregano, thyme, bay — the European cousin of spice_base
    "raw_crunch",  # eaten raw for crunch and freshness: cucumber, radish, lettuce
    "porridge_base",  # cooks into a breakfast porridge: oats, buckwheat
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
    "stone_fruit",
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
    "vinegar",
    "raising_agent",
    # Culinary classes — what you'd reach for, not botany
    "pasta",
    "noodle",
    "rice",
    "bread",  # loaves, tortillas, pastry
    "fruit",
    "sweetener",
    "starch",  # cornflour and friends
    "condiment",  # bottled sauces and pastes
    "seasoning",  # stock, salt-adjacent things
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


class Price(IntEnum):
    """What it costs to buy one pack, roughly. Not money — a feel for it."""

    NEGLIGIBLE = 0  # spices, stock cubes: you never think about it
    CHEAP = 1  # carrots, cabbage, tins of beans, pasta
    MODERATE = 2  # cauliflower, broccoli, plant milk
    PRICEY = 3  # tofu, cashews, miso
    FANCY = 4  # pine nuts, vegan parmesan

    @property
    def word(self) -> str:
        return self.name.lower()


# Natural-language names for each level, for YAML and for people typing
PRICE_WORDS: dict[str, Price] = {
    "negligible": Price.NEGLIGIBLE,
    "free": Price.NEGLIGIBLE,
    "basically free": Price.NEGLIGIBLE,
    "cheap": Price.CHEAP,
    "budget": Price.CHEAP,
    "moderate": Price.MODERATE,
    "normal": Price.MODERATE,
    "mid": Price.MODERATE,
    "average": Price.MODERATE,
    "pricey": Price.PRICEY,
    "expensive": Price.PRICEY,
    "spendy": Price.PRICEY,
    "fancy": Price.FANCY,
    "luxury": Price.FANCY,
    "treat": Price.FANCY,
}


def parse_price(value: object) -> Price:
    """0-4, or a word like "cheap" / "pricey" / "fancy"."""
    if isinstance(value, Price):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Price(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.isdigit():
            return Price(int(text))
        if text in PRICE_WORDS:
            return PRICE_WORDS[text]
    raise ValueError(
        f"price must be 0-4 or one of {sorted(set(PRICE_WORDS))}, got {value!r}"
    )


PriceLevel = Annotated[Price, BeforeValidator(parse_price)]

# Colour — used for harmony constraints in meal planner
ColourFamily = Literal[
    "green",
    "red",
    "orange",
    "yellow",
    "white",
    "brown",
    "purple",
    "black",
    "beige",
]

COLOUR_RGB: dict[str, tuple[int, int, int]] = {
    "green": (80, 140, 80),
    "red": (200, 60, 60),
    "orange": (220, 130, 50),
    "yellow": (220, 200, 80),
    "white": (240, 240, 240),
    "brown": (140, 90, 50),
    "purple": (130, 70, 160),
    "black": (30, 30, 30),
    "beige": (245, 245, 220),
}


def colour_distance(a: str, b: str) -> float:
    ra, ga, ba = COLOUR_RGB[a]
    rb, gb, bb = COLOUR_RGB[b]
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


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

CuisineContext = Literal[
    "italian",
    "french",
    "indian",
    "japanese",
    "korean",
    "levantine",
    "mexican",
    "british",
    "universal",
    "chinese",
    "eastern_european",
    "thai",
    "american",
    "mediterranean",
]

CuisineFamily = Literal[
    "european",
    "south_asian",
    "east_asian",
    "middle_eastern",
    "american",
    "universal",
]

CUISINE_FAMILY: dict[CuisineContext, CuisineFamily] = {
    "italian": "european",
    "french": "european",
    "indian": "south_asian",
    "chinese": "east_asian",
    "japanese": "east_asian",
    "korean": "east_asian",
    "levantine": "middle_eastern",
    "mexican": "american",
    "british": "european",
    "universal": "universal",
    "eastern_european": "european",
    "thai": "east_asian",
    "american": "american",
    "mediterranean": "european",
}

MealType = Literal[
    "breakfast", "lunch", "dinner", "snack", "side", "dessert", "cake", "bread"
]


class DataModel(BaseModel):
    """Base for everything loaded from YAML: unknown keys are errors, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Ingredient Class — top-level ontology node
# ---------------------------------------------------------------------------


class IngredientClass(DataModel):
    id: str
    name: str
    typical_roles: list[Role]
    typical_flavour: list[FlavourNote]
    typical_colour: ColourFamily
    notes: str = ""


# ---------------------------------------------------------------------------
# Ingredient — concrete ingredient, member of one or more classes
# ---------------------------------------------------------------------------


class Ingredient(DataModel):
    id: str
    name: str
    classes: list[IngredientClassName]
    # Roles this ingredient can play — may extend or restrict class defaults
    roles: list[Role]
    flavour: list[FlavourNote]
    colour: ColourFamily
    season: list[Season]
    price: PriceLevel
    # Grams you buy at once (a bag, a tin, a block). Defaults to unit_weight or 500g
    pack_weight: float | None = None
    cuisine_context: list[CuisineContext] = Field(default_factory=list)
    nutrition: list[NutritionFlag] = Field(default_factory=list)
    # Days a fresh one keeps once bought. None = keeps for ages (tins, dried, frozen)
    shelf_life_days: int | None = None
    # Grams of one natural unit: a carrot, a tin, a block of tofu. Lets inventory
    # say "3 carrots" instead of "240g"
    unit_weight: float | None = None
    # Grams as stocked per gram as it lands in the dish. Dried things swell:
    # 100g cooked lentils needs ~40g dried, so red_lentils has stock_ratio 0.4
    stock_ratio: float = 1.0
    # Strong flavours can only make up so much of a slot: garlic fills 8g per
    # serving of an aromatic base, not all 80g. None = no cap
    max_per_serving: float | None = None
    unlimited: bool = (
        False  # Is a cupboard staple I won't run out of, e.g. flour, nuts, spices
    )
    # Functional ingredient that can appear in several slots of one recipe without
    # breaking no_ingredient_reuse, e.g. flour in both a flatbread and a roux
    staple: bool = False
    notes: str = ""

    @property
    def perishable(self) -> bool:
        return self.shelf_life_days is not None

    @property
    def pack_grams(self) -> float:
        return self.pack_weight or self.unit_weight or 500.0


# ---------------------------------------------------------------------------
# Time — minutes of hands-on work and of waiting
# ---------------------------------------------------------------------------


class Time(DataModel):
    active: int = 0  # minutes you're actually doing something
    passive: int = 0  # minutes it simmers / roasts / soaks without you

    @property
    def total(self) -> int:
        return self.active + self.passive


# ---------------------------------------------------------------------------
# PreparedIngredient — ingredient + method = something usable in a component
# ---------------------------------------------------------------------------


class PreparedIngredient(DataModel):
    id: str  # e.g. "spinach__wilted"
    base_ingredients: list[str]
    # Grams of each base (as it lands in the dish) per gram of the prepared form.
    # Bases left out default to 1.0. Cashew cream is mostly water: {cashews: 0.4}
    base_ratio: dict[str, float] = Field(default_factory=dict)
    cuisine_context: list[CuisineContext] = Field(default_factory=list)
    method: Method
    unlocked_roles: list[Role]  # what roles this preparation enables
    flavour: list[FlavourNote] = []
    colour: ColourFamily | None = None
    time: Time = Field(default_factory=Time)
    # Can be done earlier (soaking cashews): kept off the recipe's critical path
    make_ahead: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Amount — how much of a slot a recipe needs
# ---------------------------------------------------------------------------


class Amount(DataModel):
    """Grams as it lands in the dish. Total = per_serving * servings + per_batch."""

    per_serving: float = 0.0
    per_batch: float = 0.0  # cakes and bread scale by loaf, not by person

    def grams(self, servings: int) -> float:
        return self.per_serving * servings + self.per_batch


# ---------------------------------------------------------------------------
# ComponentSlot — abstract slot in a recipe, filled by matching ingredients
# ---------------------------------------------------------------------------


class ComponentSlot(DataModel):
    id: str
    label: str  # human readable e.g. "Green Sauce Base"
    required_roles: list[Role]  # ingredient must cover ALL of these
    preferred_roles: list[Role] = Field(
        default_factory=list
    )  # Bonus if role covers these additional roles
    preferred_classes: list[IngredientClassName] = Field(default_factory=list)
    # Hard filter: ingredient must belong to at least one of these (e.g. [pasta])
    required_classes: list[IngredientClassName] = Field(default_factory=list)
    # ...and must not belong to any of these (e.g. a grain bowl: no pasta)
    excluded_classes: list[IngredientClassName] = Field(default_factory=list)
    required_method: list[Method]  # acceptable prep methods
    optional: bool = False  # if True, slot can be left empty
    max_ingredients: int = 1  # prevent slot becoming a dumping ground
    # Share the amount between ingredients (a tray of mixed veg) instead of
    # filling from the top-ranked one first
    spread: bool = False
    # How much the slot needs. "trace" = too little to track (spices, raising agents)
    amount: Amount | Literal["trace"] | None = None
    time: Time = Field(default_factory=Time)
    required_colour: ColourFamily | None = None
    colour_tolerance: float = 0.4  # 0.0 = exact, 1.0 = anything goes
    notes: str = ""


# ---------------------------------------------------------------------------
# RecipeEdge — directed connection between two component slots
# ---------------------------------------------------------------------------


class RecipeEdge(DataModel):
    from_slot: str  # ComponentSlot.id
    to_slot: str  # ComponentSlot.id
    edge_type: EdgeType


# ---------------------------------------------------------------------------
# Recipe — a DAG of component slots
# ---------------------------------------------------------------------------


class Recipe(DataModel):
    id: str
    name: str
    description: str
    slots: list[ComponentSlot]
    edges: list[RecipeEdge]
    cuisine_context: list[CuisineContext] = Field(default_factory=list)
    # Default lunch / dinner as that's most things
    meal_type: list[MealType] = Field(default_factory=lambda: ["lunch", "dinner"])  # ty: ignore
    preferred_ingredients: list[str] = Field(
        default_factory=list
    )  # Preferential ingredients
    required_ingredients: list[str] = Field(
        default_factory=list
    )  # Must be placed in a slot for the recipe to be valid
    # Hard constraints
    no_ingredient_reuse: bool = True  # same ingredient can't fill two slots
    colour_dominant: ColourFamily | None = None
    serves: int = 2
    # Real recipes this archetype was abstracted from — cook from these
    sources: list[str] = Field(default_factory=list)
    notes: str = ""
