"""
Metro Realty Corp scraper
=========================
Pulls listings from metrorealtycorp.com using the `ygl-*` class structure
discovered by inspect_structure.py.

Structure summary
-----------------
Search results page:
  Cards:          .ygl-listing-preview
  Image:          .ygl-listing-preview-image img
  Stats:          .ygl-listing-preview-bottom ul li  (price / beds / baths / sqft)
  Address lines:  .ygl-listing-preview-bottom p  (first = street, second = city+zip)
  Detail link:    .ygl-listing-preview-container a[href]  (first occurrence)
  Exclusive:      .ygl-exclusive-badge
  Pagination:     &page_index=N  (N starts at 1)

Detail page:
  Address:        .ygl-single-listing-details-left h1
  Price:          .ygl-single-listing-details-left h2  ("... | $4100/month")
  Detail fields:  .ygl-single-listing-details-left ul li.xcol  (key: strong, val: span)
                  Includes: Neighborhood, Available Date
  Beds/baths:     .ygl-single-listing-details-keys ul li  (key: strong, val: span)
  Photos:         img[src*="ygl-photos.s3"]

Run manually (once a day at most):
    python -m scraper.metro_scraper
Or via the web UI:
    POST /api/scrape
"""

import os
import re
import json
import time
import logging
from curl_cffi import requests as cffi
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

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

IMAGE_DIR = Path(__file__).parent.parent / "static" / "images" / "apartments"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_DELAY = 3.0   # seconds between page requests


# ── HTTP session — impersonates Chrome TLS fingerprint ────────────────────────
# curl_cffi uses curl's actual TLS stack so WAFs can't distinguish it from
# a real browser, unlike the standard `requests` library.

session = cffi.Session(impersonate="chrome124")


def fetch(url: str, retries: int = 2) -> str:
    """Fetch a URL with retries on 429/5xx."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                logger.warning("Rate limited — waiting %ds before retry %d/%d", wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt == retries:
                raise
            logger.warning("Fetch failed (%s), retrying in 5s…", e)
            time.sleep(5)
    return ""


# ── Boundary helpers ───────────────────────────────────────────────────────────

def point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
    """Ray-casting algorithm. polygon is [[lat, lng], ...]."""
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


# ── Coordinate extraction (from Metro Realty's own map data) ──────────────────

def extract_markers(soup) -> dict:
    """Parse data-ygl-markers from a search results page.
    Returns {source_id: (lat, lng)}.
    """
    coords = {}
    el = soup.select_one("[data-ygl-markers]")
    if not el:
        return coords
    for m in json.loads(el["data-ygl-markers"]):
        sid = urlparse(m["url"]).path.strip("/").split("/")[-1]
        coords[sid] = (float(m["data"]["lat"]), float(m["data"]["lng"]))
    return coords


def extract_detail_coord(soup) -> tuple | None:
    """Parse data-ygl-map from a listing detail page. Fallback."""
    el = soup.select_one("[data-ygl-map]")
    if not el:
        return None
    data = json.loads(el["data-ygl-map"])
    return float(data["lat"]), float(data["lng"])


# ── Image download ─────────────────────────────────────────────────────────────

def download_image(image_url: str, source_id: str, idx: int) -> str | None:
    """Download one image and save locally. Returns the web-accessible path."""
    try:
        time.sleep(1.0)
        resp = session.get(image_url, timeout=15)
        resp.raise_for_status()
        ext = image_url.split(".")[-1].split("?")[0].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        filename = f"{source_id}_{idx}.{ext}"
        path = IMAGE_DIR / filename
        with open(path, "wb") as f:
            f.write(resp.content)
        logger.debug("Saved image: %s", filename)
        return f"/images/apartments/{filename}"
    except Exception as e:
        logger.warning("Image download failed %s: %s", image_url, e)
        return None


# ── Search page parser ─────────────────────────────────────────────────────────

def parse_card(card) -> dict | None:
    """Parse one .ygl-listing-preview card from the search results."""
    try:
        # Detail URL & source ID (use URL slug as stable identifier)
        link_el = card.select_one(".ygl-listing-preview-container > div > a, "
                                   ".ygl-listing-preview-image a")
        if not link_el:
            return None
        detail_url = link_el.get("href", "")
        if not detail_url.startswith("http"):
            detail_url = urljoin(BASE_URL, detail_url)
        source_id = urlparse(detail_url).path.strip("/").split("/")[-1]

        # Thumbnail
        img_el = card.select_one(".ygl-listing-preview-image img")
        thumbnail_url = img_el["src"] if img_el else None

        # Stats list: Price / Beds / Baths / Sq Ft
        stats: dict[str, str] = {}
        for li in card.select(".ygl-listing-preview-bottom ul li"):
            val_el  = li.select_one("strong")
            key_el  = li.select_one("span")
            if val_el and key_el:
                stats[key_el.get_text(strip=True).lower()] = val_el.get_text(strip=True)

        def to_num(s):
            s = re.sub(r"[^\d.]", "", s or "")
            try: return float(s) if s else None
            except ValueError: return None

        rent_str  = stats.get("month", "")
        rent      = int(to_num(rent_str) or 0)
        bedrooms  = to_num(stats.get("beds"))
        bathrooms = to_num(stats.get("baths"))
        raw_sqft  = to_num(stats.get("sq ft"))
        sqft      = int(raw_sqft) if raw_sqft and raw_sqft < 9000 else None  # 9999 = unknown

        # Address paragraphs in .ygl-listing-preview-bottom
        bottom = card.select_one(".ygl-listing-preview-bottom")
        addr_paras = bottom.select("p") if bottom else []
        street  = addr_paras[0].get_text(strip=True) if len(addr_paras) > 0 else ""
        cityzip = addr_paras[1].get_text(strip=True) if len(addr_paras) > 1 else "Boston MA"
        address = f"{street}, {cityzip}" if street else cityzip

        # Badges
        no_fee   = bool(card.select_one(".ygl-no-fee-badge, [class*='no-fee']") or
                        card.find(string=re.compile(r"no.?fee", re.I)))
        exclusive = bool(card.select_one(".ygl-exclusive-badge"))

        return {
            "source_id":    source_id,
            "source_url":   detail_url,
            "address":      address,
            "rent":         rent,
            "bedrooms":     bedrooms,
            "bathrooms":    bathrooms,
            "sqft":         sqft,
            "thumbnail_url": thumbnail_url,
            "no_fee":       no_fee,
            "exclusive":    exclusive,
        }
    except Exception as e:
        logger.warning("Failed to parse card: %s", e)
        return None


# ── Detail page parser ─────────────────────────────────────────────────────────

def scrape_detail(url: str) -> dict:
    """Fetch listing detail page and extract extra fields + all photos."""
    extra = {
        "address_full":   None,
        "neighborhood":   None,
        "available_from": None,
        "zip_code":       None,
        "images_raw":     [],
        "_coord":         None,   # (lat, lng) from data-ygl-map, fallback only
    }
    try:
        time.sleep(REQUEST_DELAY)
        html = fetch(url)
        soup = BeautifulSoup(html, "lxml")

        # Full address from h1
        h1 = soup.select_one(".ygl-single-listing-details-left h1")
        if h1:
            extra["address_full"] = h1.get_text(strip=True)
            # Extract zip from address
            m = re.search(r"\b(\d{5})\b", extra["address_full"])
            if m:
                extra["zip_code"] = m.group(1)

        # Detail key/value pairs (Neighborhood, Available Date, etc.)
        for li in soup.select(".ygl-single-listing-details-left ul li.xcol"):
            key = li.select_one("strong")
            val = li.select_one("span")
            if not key or not val:
                continue
            k = key.get_text(strip=True).lower()
            v = val.get_text(strip=True)
            if "neighborhood" in k:
                extra["neighborhood"] = v
            elif "available" in k:
                extra["available_from"] = v

        # Exact coordinate from detail page map (fallback if not in search markers)
        coord = extract_detail_coord(soup)
        if coord:
            extra["_coord"] = coord

        # Listing photos (hosted on ygl-photos S3)
        for img in soup.select("img[src*='ygl-photos']"):
            src = img.get("src", "")
            if src and src not in extra["images_raw"]:
                extra["images_raw"].append(src)

    except Exception as e:
        logger.warning("Detail scrape failed %s: %s", url, e)

    return extra


# ── Main scrape ────────────────────────────────────────────────────────────────

def run_scrape(boundary: dict | None = None) -> int:
    """Full scrape: search results → detail pages → geocode → store in DB."""
    from models import db, Apartment

    logger.info("=== Metro Realty scrape started — %s ===", datetime.utcnow().isoformat())
    new_count = 0
    boundary_latlngs = (boundary or {}).get("latlngs")

    # ── Pass 1: collect all exact coordinates from the map markers on every
    #           search results page before touching any detail pages ──────────
    all_coords: dict[str, tuple] = {}
    page = 1
    while True:
        url = SEARCH_URL + (f"&page_index={page}" if page > 1 else "")
        logger.info("Collecting markers from search page %d…", page)
        try:
            time.sleep(REQUEST_DELAY)
            html = fetch(url)
        except Exception as e:
            logger.error("Cannot fetch page %d: %s", page, e)
            break
        soup = BeautifulSoup(html, "lxml")
        coords = extract_markers(soup)
        logger.info("  %d markers on page %d", len(coords), page)
        all_coords.update(coords)
        next_page_url = f"&page_index={page + 1}"
        if not any(next_page_url in (a.get("href","") or "") for a in soup.select(".ygl-pagination a")):
            break
        page += 1
    logger.info("Total exact coords collected: %d", len(all_coords))

    # ── Pass 2: scrape cards + detail pages, attach coords ───────────────────
    page = 1
    while True:
        url = SEARCH_URL + (f"&page_index={page}" if page > 1 else "")
        logger.info("Fetching search results page %d", page)

        try:
            time.sleep(REQUEST_DELAY)
            html = fetch(url)
        except Exception as e:
            logger.error("Cannot fetch page %d: %s", page, e)
            break

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(".ygl-listing-preview")
        logger.info("  Found %d cards on page %d", len(cards), page)

        if not cards:
            break

        for card in cards:
            listing = parse_card(card)
            if not listing or not listing.get("source_id"):
                continue

            # Skip duplicates
            if Apartment.query.filter_by(source_id=listing["source_id"]).first():
                logger.debug("Already stored: %s", listing["source_id"])
                continue

            # Detail page (for images, neighborhood, availability)
            detail = scrape_detail(listing["source_url"])
            full_addr = detail.get("address_full") or listing["address"]

            # Exact coordinates from Metro Realty's own map data
            lat, lng = all_coords.get(listing["source_id"], (None, None))
            if lat is None:
                # Fallback: detail page data-ygl-map (shouldn't normally be needed)
                coord = detail.get("_coord")
                if coord:
                    lat, lng = coord
            listing["latitude"]  = lat
            listing["longitude"] = lng

            # Boundary filter
            within = None
            if lat and lng and boundary_latlngs:
                within = point_in_polygon(lat, lng, boundary_latlngs)
            listing["within_boundary"] = within

            # Download images (cap at 12)
            local_images = []
            for i, img_url in enumerate(detail.get("images_raw", [])[:12]):
                path = download_image(img_url, listing["source_id"], i)
                if path:
                    local_images.append(path)
            # Fallback to thumbnail from search results card
            if not local_images and listing.get("thumbnail_url"):
                path = download_image(listing["thumbnail_url"], listing["source_id"], 0)
                if path:
                    local_images.append(path)

            # Persist
            apt = Apartment(
                source_id       = listing["source_id"],
                source_url      = listing["source_url"],
                address         = full_addr,
                zip_code        = detail.get("zip_code"),
                neighborhood    = detail.get("neighborhood"),
                bedrooms        = listing["bedrooms"],
                bathrooms       = listing["bathrooms"],
                sqft            = listing["sqft"],
                rent            = listing["rent"],
                no_fee          = listing["no_fee"],
                available_from  = detail.get("available_from"),
                latitude        = lat,
                longitude       = lng,
                within_boundary = within,
                images_json     = json.dumps(local_images),
            )
            db.session.add(apt)
            db.session.commit()
            new_count += 1
            logger.info(
                "  + %-50s $%d  within=%s  images=%d",
                full_addr[:50], listing["rent"], within, len(local_images)
            )

        # Check for next page link
        next_link = soup.select_one(".ygl-pagination a[href*='page_index']:-soup-contains('Next'), "
                                     ".ygl-pagination a[rel='next']")
        if not next_link:
            # Also check if the page had a page_index=N+1 link at all
            next_page_url = f"&page_index={page + 1}"
            has_next = any(
                next_page_url in (a.get("href", "") or "")
                for a in soup.select(".ygl-pagination a")
            )
            if not has_next:
                logger.info("No more pages after page %d", page)
                break
        page += 1

    logger.info("=== Scrape complete. New listings added: %d ===", new_count)
    return new_count


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from app import app, db
    with app.app_context():
        db.create_all()
        boundary_file = Path(__file__).parent.parent / "data" / "boundary.json"
        boundary = json.loads(boundary_file.read_text()) if boundary_file.exists() else None
        count = run_scrape(boundary)
        print(f"\nDone. New listings added: {count}")
