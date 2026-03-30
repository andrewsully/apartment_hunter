"""
Metro Realty Corp scraper
=========================
Pulls listings from metrorealtycorp.com, geocodes addresses, downloads images,
and stores everything in the local SQLite database.

Run manually (sparingly — once a day at most) or call run_scrape() via the
/api/scrape endpoint in app.py.

Usage:
    python -m scraper.metro_scraper
"""

import os
import re
import json
import time
import hashlib
import logging
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL = "https://metrorealtycorp.com"

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
}

IMAGE_DIR = Path(__file__).parent.parent / "static" / "images" / "apartments"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# Polite delay between requests (seconds)
REQUEST_DELAY = 2.5


# ── Boundary helpers ───────────────────────────────────────────────────────────

def point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test.
    polygon is a list of [lat, lng] pairs.
    """
    n = len(polygon)
    inside = False
    x, y = lng, lat
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][1], polygon[i][0]
        xj, yj = polygon[j][1], polygon[j][0]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── Geocoding ──────────────────────────────────────────────────────────────────

def geocode_address(address: str, city: str = "Boston", state: str = "MA") -> tuple[float | None, float | None]:
    """Use OpenStreetMap Nominatim (free, no API key) to geocode an address.
    Respects the 1 req/sec usage policy.
    """
    query = f"{address}, {city}, {state}"
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    geocode_headers = {**HEADERS, "Referer": "https://github.com/andrewsully/apartment_hunter"}
    try:
        time.sleep(1.1)  # Nominatim rate limit
        resp = requests.get(url, params=params, headers=geocode_headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        logger.warning("Geocoding failed for %s: %s", query, e)
    return None, None


# ── Image download ─────────────────────────────────────────────────────────────

def download_image(image_url: str, source_id: str, idx: int) -> str | None:
    """Download an image and store it locally. Returns the local relative path."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(image_url, headers=HEADERS, timeout=15, stream=True)
        resp.raise_for_status()
        ext = image_url.split(".")[-1].split("?")[0].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        filename = f"{source_id}_{idx}.{ext}"
        path = IMAGE_DIR / filename
        with open(path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return f"/images/apartments/{filename}"
    except Exception as e:
        logger.warning("Image download failed %s: %s", image_url, e)
        return None


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_listing_card(card) -> dict | None:
    """Parse a single listing card from the search results page."""
    try:
        link_el = card.select_one("a[href]")
        if not link_el:
            return None
        detail_url = urljoin(BASE_URL, link_el["href"])
        source_id = hashlib.md5(detail_url.encode()).hexdigest()[:12]

        rent_el = card.select_one(".rent, [class*='rent'], [class*='price']")
        beds_el = card.select_one(".beds, [class*='beds'], [class*='bed']")
        baths_el = card.select_one(".baths, [class*='baths'], [class*='bath']")
        addr_el = card.select_one("address, [class*='address'], [class*='street']")
        sqft_el = card.select_one("[class*='sqft'], [class*='sq']")
        img_el = card.select_one("img[src]")

        def extract_num(el):
            if not el:
                return None
            txt = re.sub(r"[^\d.]", "", el.get_text())
            try:
                return float(txt) if txt else None
            except ValueError:
                return None

        return {
            "source_id": source_id,
            "source_url": detail_url,
            "address": addr_el.get_text(strip=True) if addr_el else "",
            "rent": int(extract_num(rent_el) or 0),
            "bedrooms": extract_num(beds_el),
            "bathrooms": extract_num(baths_el),
            "sqft": int(extract_num(sqft_el) or 0) or None,
            "thumbnail_url": img_el["src"] if img_el else None,
            "no_fee": bool(card.find(string=re.compile(r"no.?fee", re.I))),
        }
    except Exception as e:
        logger.warning("Failed to parse card: %s", e)
        return None


def scrape_detail_page(url: str) -> dict:
    """Fetch a listing detail page for extra info (images, availability, etc.)."""
    extra = {"images_raw": [], "available_from": None, "available_to": None, "neighborhood": None}
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # All images
        for img in soup.select("img[src]"):
            src = img["src"]
            if any(k in src.lower() for k in ("listing", "property", "photo", "apt", "unit")):
                extra["images_raw"].append(src)

        # Availability text
        avail_text = soup.find(string=re.compile(r"avail", re.I))
        if avail_text:
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", avail_text)
            if len(dates) >= 1:
                extra["available_from"] = dates[0]
            if len(dates) >= 2:
                extra["available_to"] = dates[1]

        # Neighborhood
        neigh_el = soup.select_one("[class*='neighborhood'], [class*='area']")
        if neigh_el:
            extra["neighborhood"] = neigh_el.get_text(strip=True)

    except Exception as e:
        logger.warning("Detail page failed %s: %s", url, e)
    return extra


# ── Main scrape ────────────────────────────────────────────────────────────────

def run_scrape(boundary: dict | None = None) -> int:
    """Scrape Metro Realty, geocode, filter by boundary, store in DB.
    Returns number of new listings added.
    """
    # Import here to avoid circular imports (Flask app context needed)
    from models import db, Apartment
    from flask import current_app

    logger.info("Starting Metro Realty scrape — %s", datetime.utcnow().isoformat())
    new_count = 0
    page = 1

    boundary_latlngs = boundary.get("latlngs") if boundary else None

    while True:
        url = SEARCH_URL + (f"&paged={page}" if page > 1 else "")
        logger.info("Fetching page %d: %s", page, url)
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch listing page %d: %s", page, e)
            break

        soup = BeautifulSoup(resp.text, "lxml")

        # Try common card selectors — adjust based on actual site structure
        cards = (
            soup.select(".listing-card")
            or soup.select(".property-card")
            or soup.select("[class*='listing']")
            or soup.select("article")
        )

        if not cards:
            logger.info("No cards found on page %d — stopping.", page)
            break

        for card in cards:
            listing = parse_listing_card(card)
            if not listing or not listing["address"]:
                continue

            # Skip if already in DB
            existing = Apartment.query.filter_by(source_id=listing["source_id"]).first()
            if existing:
                logger.debug("Already stored: %s", listing["source_id"])
                continue

            # Geocode
            lat, lng = geocode_address(listing["address"])
            listing["latitude"] = lat
            listing["longitude"] = lng

            # Boundary check
            within = None
            if lat and lng and boundary_latlngs:
                within = point_in_polygon(lat, lng, boundary_latlngs)
            listing["within_boundary"] = within

            # Scrape detail page for images & extras
            detail = scrape_detail_page(listing["source_url"])
            listing.update({k: v for k, v in detail.items() if k != "images_raw"})

            # Download images
            local_images = []
            for i, img_url in enumerate(detail.get("images_raw", [])[:8]):  # cap at 8
                local_path = download_image(img_url, listing["source_id"], i)
                if local_path:
                    local_images.append(local_path)
            # Fall back to thumbnail
            if not local_images and listing.get("thumbnail_url"):
                local_path = download_image(listing["thumbnail_url"], listing["source_id"], 0)
                if local_path:
                    local_images.append(local_path)

            # Persist
            apt = Apartment(
                source_id=listing["source_id"],
                source_url=listing["source_url"],
                address=listing["address"],
                bedrooms=listing.get("bedrooms"),
                bathrooms=listing.get("bathrooms"),
                sqft=listing.get("sqft"),
                rent=listing.get("rent"),
                no_fee=listing.get("no_fee", False),
                available_from=listing.get("available_from"),
                available_to=listing.get("available_to"),
                neighborhood=listing.get("neighborhood"),
                latitude=lat,
                longitude=lng,
                within_boundary=within,
                images_json=json.dumps(local_images),
            )
            db.session.add(apt)
            db.session.commit()
            new_count += 1
            logger.info("Stored: %s — $%d — within_boundary=%s", listing["address"], listing["rent"], within)

        # Check for next page
        next_link = soup.select_one("a[class*='next'], .next a, [rel='next']")
        if not next_link:
            break
        page += 1

    logger.info("Scrape complete. New listings: %d", new_count)
    return new_count


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from app import app, db
    from models import Apartment

    with app.app_context():
        db.create_all()
        boundary_file = Path(__file__).parent.parent / "data" / "boundary.json"
        boundary = json.loads(boundary_file.read_text()) if boundary_file.exists() else None
        count = run_scrape(boundary)
        print(f"Done. New listings added: {count}")
