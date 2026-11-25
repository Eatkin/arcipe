"""
Parse cached pages (corpus/pages) into corpus/recipes.json.

    uv run python tools/corpus/parse.py

Each recipe: url, source, title, labels, serves, minutes, ingredients (raw lines),
method (text). Uses schema.org Recipe JSON-LD where a site has it, otherwise a
heuristic for Blogger posts (vegansandra.com): the run of short, quantity-ish
lines after "Serves:", ended by the first long paragraph.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"
PAGES = CORPUS / "pages"
INDEX = CORPUS / "index.json"
OUT = CORPUS / "recipes.json"

QUANTITY = re.compile(
    r"(^|\s)(\d|½|¼|¾|⅓|⅔|⅛|a |an |one |two |three |half |pinch|handful|dash|splash|few )",
    re.IGNORECASE,
)
UNIT = re.compile(
    r"\b(cups?|tbsp|tsp|tl|g|kg|ml|dl|l|oz|lbs?|cans?|tins?|cloves?|pinch|handful|bunch|"
    r"tablespoons?|teaspoons?|grams?|heaping|medium|large|small)\b",
    re.IGNORECASE,
)


def strip_tags(fragment: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.S | re.I)
    t = re.sub(r"<(br|/p|/li|/h\d|/div|li|p|h\d|tr)[^>]*>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------


def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def _minutes(iso: str | None) -> int | None:
    if not iso:
        return None
    m = re.match(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?", str(iso).replace("PT", "PT"))
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def _instructions(raw) -> str:
    parts: list[str] = []
    for node in raw if isinstance(raw, list) else [raw]:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            if "itemListElement" in node:
                parts.append(_instructions(node["itemListElement"]))
            elif node.get("text"):
                parts.append(node["text"])
    return strip_tags(" ".join(parts))


def from_jsonld(page: str) -> dict | None:
    for block in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, flags=re.S | re.I
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if not _is_recipe(node):
                continue
            yld = node.get("recipeYield")
            if isinstance(yld, list):
                yld = yld[0] if yld else None
            serves = re.search(r"\d+", str(yld)) if yld else None
            keywords = node.get("keywords") or ""
            if isinstance(keywords, list):
                keywords = ",".join(keywords)
            cats = []
            for key in ("recipeCategory", "recipeCuisine"):
                v = node.get(key) or []
                cats += v if isinstance(v, list) else [v]
            return {
                "title": html.unescape(str(node.get("name", ""))).strip(),
                "labels": [c for c in cats if c]
                + [k.strip() for k in str(keywords).split(",") if k.strip()],
                "serves": int(serves.group()) if serves else None,
                "minutes": _minutes(node.get("totalTime") or node.get("cookTime")),
                "ingredients": [
                    strip_tags(str(i)) for i in node.get("recipeIngredient") or []
                ],
                "method": _instructions(node.get("recipeInstructions") or ""),
            }
    return None


# ---------------------------------------------------------------------------
# Blogger (vegansandra.com)
# ---------------------------------------------------------------------------


def _ingredient_like(line: str) -> bool:
    if len(line) > 120 or line.endswith(":"):
        return False
    return bool(QUANTITY.search(line) or UNIT.search(line) or len(line.split()) <= 4)


def from_blogger(page: str) -> dict | None:  # noqa: C901 — heuristic scraper
    title_m = re.search(
        r"<meta content='([^']+)' property='og:title'", page
    ) or re.search(r"<h3 class=.post-title[^>]*>(.*?)</h3>", page, flags=re.S)
    body_start = page.find("post-body entry-content")
    if body_start < 0:
        return None
    body_start = page.find(">", body_start) + 1
    body_end = page.find("post-footer", body_start)
    text = strip_tags(page[body_start:body_end])
    lines = text.split("\n")

    serves = minutes = None
    start = None
    for i, ln in enumerate(lines):
        if m := re.match(r"(?i)(?:serves|makes|yield)\s*:?\D{0,30}?(\d+)", ln):
            serves = int(m.group(1))
            start = i + 1
        elif m := re.match(
            r"(?i)[\w ]{0,15}time\s*:?\s*(?:(\d+)\s*h\w*)?\s*(\d+)?\s*min", ln
        ):
            minutes = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
            start = start or i + 1
    for i, ln in enumerate(lines):
        if re.fullmatch(r"(?i)\s*ingredients\s*:?\s*", ln):
            start = i + 1  # an explicit header beats "Serves: ..." and notes
            break
    if start is None:
        # No header: take the longest run of ingredient-like lines
        best, run_start = (0, 0, 0), None
        # (a run starts on a quantity, can carry "salt to taste" lines, and
        # needs a few quantities in it to count)
        measured = 0
        for i, ln in enumerate([*lines, "x" * 200]):
            if _ingredient_like(ln) and (run_start is not None or QUANTITY.search(ln)):
                run_start = i if run_start is None else run_start
                measured += bool(QUANTITY.search(ln))
            elif run_start is not None:
                if i - run_start > best[0] and measured >= 3:
                    best = (i - run_start, run_start, i)
                run_start, measured = None, 0
        if best[0] < 4:
            return None
        start = best[1]

    ingredients: list[str] = []
    end = start
    for end in range(start, len(lines)):
        ln = lines[end].strip(" ·•")
        low = ln.lower()
        if (len(ln) > 120 and not ln.endswith(":")) or re.fullmatch(
            r"(directions|instructions|method|preparation|how to make it)\s*:?", low
        ):
            break
        if not ln or re.match(
            r"(serves|makes|yield|ingredients|tip:|[\w ]*time\s*:)", low
        ):
            continue
        if ln.endswith(":"):
            continue  # "For the sauce:" sub-headers
        # older posts split "2 cups /" and "400 g chickpeas" over two lines
        if ingredients and (
            ingredients[-1].endswith(("/", "of"))
            or re.fullmatch(
                r"[\d½¼¾⅓⅔ .,/-]*\s*(tbsp|tsp|cups?|g|ml|pinch of)?",
                ingredients[-1],
                re.I,
            )
        ):
            ingredients[-1] += " " + ln
            continue
        ingredients.append(ln)
    method = " ".join(ln for ln in lines[end:] if len(ln) > 40)
    labels = [
        html.unescape(x).strip()
        for x in re.findall(r"rel=[\"']tag[\"']>([^<]+)</a>", page)
    ]
    if len(ingredients) < 3:
        return None
    return {
        "title": strip_tags(title_m.group(1)).split("\n")[0].strip() if title_m else "",
        "labels": [html.unescape(x).strip() for x in labels],
        "serves": serves,
        "minutes": minutes,
        "ingredients": ingredients,
        "method": method[:4000],
    }


def main() -> None:
    index = json.loads(INDEX.read_text())
    recipes, skipped = [], []
    for url, meta in sorted(index.items()):
        if meta.get("status") != "ok":
            continue
        page = (PAGES / meta["file"]).read_text(encoding="utf-8", errors="replace")
        parsed = from_jsonld(page) or from_blogger(page)
        if parsed is None:
            skipped.append(url)
            continue
        recipes.append({"url": url, "source": meta["source"], **parsed})
    OUT.write_text(json.dumps(recipes, indent=1, ensure_ascii=False))
    print(f"{len(recipes)} recipes parsed, {len(skipped)} pages skipped (not recipes?)")
    for url in skipped[:40]:
        print("  -", url)


if __name__ == "__main__":
    main()
