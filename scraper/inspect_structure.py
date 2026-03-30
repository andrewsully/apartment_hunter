"""
inspect_structure.py
====================
Fetches one Metro Realty search results page and one listing detail page,
then prints the full HTML tree in a readable, condensed format.

Run from project root:
    python -m scraper.inspect_structure

Output files written to scraper/output/:
    search_page.html    -- raw search results HTML
    detail_page.html    -- raw detail page HTML
    search_tree.txt     -- condensed tag tree for the search page
    detail_tree.txt     -- condensed tag tree for the detail page
"""

import time
import textwrap
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

# ── Config ────────────────────────────────────────────────────────────────────

SEARCH_URL = (
    "https://metrorealtycorp.com/rentals/"
    "?city_neighborhood=Boston%3ABack+Bay%2CBoston%3AFenway%2CBoston%3ASouth+End"
    "&min_rent=4000&max_rent=6000"
    "&beds%5B3%5D=3&beds%5B4%5D=4"
    "&avail_from=08%2F16%2F26&avail_to=09%2F05%2F26"
    "&sort_name=rent&sort_dir=asc"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

OUT_DIR = Path(__file__).parent / "output"
OUT_DIR.mkdir(exist_ok=True)

# ── Fetch helpers ──────────────────────────────────────────────────────────────

def fetch(url: str) -> str:
    print(f"  GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


# ── Tree printer ───────────────────────────────────────────────────────────────

def tag_summary(tag: Tag) -> str:
    """One-line summary: <tag#id.class1.class2 attr=val …> [text snippet]"""
    parts = [f"<{tag.name}"]
    if tag.get("id"):
        parts.append(f"  #{tag['id']}")
    if tag.get("class"):
        parts.append("  ." + ".".join(tag["class"]))
    for attr in ("href", "src", "type", "name", "data-id", "data-listing-id",
                 "data-rent", "data-beds", "data-address"):
        if tag.get(attr):
            val = tag[attr]
            if isinstance(val, list):
                val = " ".join(val)
            parts.append(f"  {attr}={val[:60]!r}")
    parts.append(">")
    text = tag.get_text(" ", strip=True)
    if text:
        snippet = text[:80].replace("\n", " ")
        parts.append(f'  "{snippet}{"…" if len(text) > 80 else ""}"')
    return "".join(parts)


def print_tree(tag: Tag, lines: list[str], depth: int = 0, max_depth: int = 8) -> None:
    if depth > max_depth:
        return
    indent = "  " * depth
    if not isinstance(tag, Tag):
        return
    lines.append(f"{indent}{tag_summary(tag)}")
    for child in tag.children:
        if isinstance(child, Tag):
            print_tree(child, lines, depth + 1, max_depth)


def build_tree(soup: BeautifulSoup, root_selector: str | None = None) -> str:
    lines: list[str] = []
    root = soup.select_one(root_selector) if root_selector else soup.body or soup
    if root:
        print_tree(root, lines)
    return "\n".join(lines)


# ── Listing card extractor ─────────────────────────────────────────────────────

CARD_SELECTORS = [
    ".listing-card", ".property-card", ".rental-listing",
    ".listing-item", ".apt-listing", "[class*='listing']",
    "[class*='property']", "[class*='rental']",
    "article", ".card",
]

def find_cards(soup: BeautifulSoup):
    for sel in CARD_SELECTORS:
        cards = soup.select(sel)
        if len(cards) >= 2:
            return sel, cards
    return None, []


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    sep = "=" * 80

    # ── 1. Search results page ──
    print(f"\n{sep}")
    print("STEP 1: Fetching search results page")
    print(sep)
    search_html = fetch(SEARCH_URL)
    (OUT_DIR / "search_page.html").write_text(search_html, encoding="utf-8")
    print(f"  Saved → scraper/output/search_page.html  ({len(search_html):,} bytes)")

    soup_search = BeautifulSoup(search_html, "lxml")

    # Find listing cards
    sel, cards = find_cards(soup_search)
    print(f"\n  Card selector matched: {sel!r}  ({len(cards)} cards found)")

    # Print all class names on <body> descendants to help identify card wrapper
    print("\n  All unique class names on the page:")
    all_classes = set()
    for tag in soup_search.find_all(True):
        for c in (tag.get("class") or []):
            all_classes.add(c)
    for c in sorted(all_classes):
        print(f"    .{c}")

    # Print first card's full tree
    if cards:
        print(f"\n{sep}")
        print(f"FIRST LISTING CARD  (selector: {sel!r})")
        print(sep)
        card_lines: list[str] = []
        print_tree(cards[0], card_lines, max_depth=10)
        card_text = "\n".join(card_lines)
        print(card_text)
        (OUT_DIR / "first_card_tree.txt").write_text(card_text, encoding="utf-8")
        print(f"\n  Saved → scraper/output/first_card_tree.txt")

    # Full search page tree (body only, depth 6)
    search_tree = build_tree(soup_search)
    (OUT_DIR / "search_tree.txt").write_text(search_tree, encoding="utf-8")
    print(f"\n  Full search page tree saved → scraper/output/search_tree.txt")

    # ── 2. Grab first detail link ──
    detail_url = None
    for a in soup_search.select("a[href]"):
        href = a["href"]
        if "/rentals/" in href and href != SEARCH_URL and len(href) > 40:
            detail_url = href if href.startswith("http") else f"https://metrorealtycorp.com{href}"
            break

    if not detail_url:
        print("\n  Could not find a detail page link — check search_page.html manually.")
        return

    print(f"\n{sep}")
    print("STEP 2: Fetching listing detail page")
    print(sep)
    time.sleep(2)
    detail_html = fetch(detail_url)
    (OUT_DIR / "detail_page.html").write_text(detail_html, encoding="utf-8")
    print(f"  Saved → scraper/output/detail_page.html  ({len(detail_html):,} bytes)")

    soup_detail = BeautifulSoup(detail_html, "lxml")

    # Print all class names on detail page
    print("\n  All unique class names on detail page:")
    detail_classes = set()
    for tag in soup_detail.find_all(True):
        for c in (tag.get("class") or []):
            detail_classes.add(c)
    for c in sorted(detail_classes):
        print(f"    .{c}")

    # Full detail page tree
    detail_tree = build_tree(soup_detail)
    (OUT_DIR / "detail_tree.txt").write_text(detail_tree, encoding="utf-8")
    print(f"\n  Full detail tree saved → scraper/output/detail_tree.txt")

    # Find and print images
    print("\n  Image tags found on detail page:")
    for img in soup_detail.select("img[src]"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        cls = " ".join(img.get("class", []))
        print(f"    src={src[:100]!r}  alt={alt!r}  class={cls!r}")

    # Find and print all text-containing elements that look like listing data
    print("\n  Possible data fields (text nodes near 'bed', 'bath', 'rent', '$'):")
    for tag in soup_detail.find_all(True):
        txt = tag.get_text(strip=True)
        if any(kw in txt.lower() for kw in ("bed", "bath", "rent", "$", "sqft", "sq ft", "available", "fee")):
            if 2 < len(txt) < 120 and not tag.find(True):
                classes = " ".join(tag.get("class") or [])
                print(f"    <{tag.name}.{classes}> {txt!r}")

    print(f"\n{sep}")
    print("Done. Check scraper/output/ for all saved files.")
    print(sep)


if __name__ == "__main__":
    main()
