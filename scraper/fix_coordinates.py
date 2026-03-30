"""
fix_coordinates.py
==================
Replaces the Nominatim-estimated coordinates in the DB with the precise
lat/lng values that Metro Realty embeds in their own map.

Sources (in priority order):
  1. data-ygl-markers  on the search results page  — one JSON blob for all listings
  2. data-ygl-map      on each listing detail page  — per-listing fallback

Run once to patch all existing records:
    python -m scraper.fix_coordinates
"""

import sys
import json
import time
import re
import logging
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

SEARCH_URL = (
    "https://metrorealtycorp.com/rentals/"
    "?city_neighborhood=Boston%3ABack+Bay%2CBoston%3AFenway%2CBoston%3ASouth+End"
    "&min_rent=4000&max_rent=6000"
    "&beds%5B3%5D=3&beds%5B4%5D=4"
    "&avail_from=08%2F16%2F26&avail_to=09%2F05%2F26"
    "&sort_name=rent&sort_dir=asc"
)

session = cffi.Session(impersonate="chrome124")


def fetch(url: str) -> str:
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def extract_markers_from_page(html: str) -> dict:
    """Parse data-ygl-markers → {source_id: (lat, lng)}."""
    soup = BeautifulSoup(html, "lxml")
    coords = {}
    map_div = soup.select_one("[data-ygl-markers]")
    if not map_div:
        return coords
    markers = json.loads(map_div["data-ygl-markers"])
    for m in markers:
        source_id = urlparse(m["url"]).path.strip("/").split("/")[-1]
        coords[source_id] = (float(m["data"]["lat"]), float(m["data"]["lng"]))
    return coords


def extract_coord_from_detail(html: str) -> tuple | None:
    """Parse data-ygl-map on detail page → (lat, lng)."""
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one("[data-ygl-map]")
    if not el:
        return None
    data = json.loads(el["data-ygl-map"])
    return float(data["lat"]), float(data["lng"])


def point_in_polygon(lat: float, lng: float, polygon: list) -> bool:
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


def main():
    from app import app, db
    from models import Apartment

    boundary_file = Path(__file__).parent.parent / "data" / "boundary.json"
    boundary_latlngs = json.loads(boundary_file.read_text())["latlngs"] if boundary_file.exists() else None

    with app.app_context():
        db.create_all()
        all_apts = Apartment.query.all()
        logger.info("Total apartments in DB: %d", len(all_apts))

        # ── Step 1: collect exact coords from all search result pages ──
        all_coords: dict[str, tuple] = {}

        page = 1
        while True:
            url = SEARCH_URL + (f"&page_index={page}" if page > 1 else "")
            logger.info("Fetching search page %d for markers…", page)
            time.sleep(2)
            html = fetch(url)
            coords = extract_markers_from_page(html)
            logger.info("  Got %d markers", len(coords))
            all_coords.update(coords)

            # Check if there's a next page
            soup = BeautifulSoup(html, "lxml")
            next_page_url = f"&page_index={page + 1}"
            has_next = any(
                next_page_url in (a.get("href", "") or "")
                for a in soup.select(".ygl-pagination a")
            )
            if not has_next:
                break
            page += 1

        logger.info("Total coords collected from search pages: %d", len(all_coords))

        # ── Step 2: update each apartment ──
        updated = 0
        detail_fallback = 0
        missing = 0

        for apt in all_apts:
            if apt.source_id in all_coords:
                lat, lng = all_coords[apt.source_id]
            else:
                # Fallback: fetch detail page for its data-ygl-map
                logger.info("  Detail fallback for: %s", apt.source_id)
                time.sleep(2)
                try:
                    html = fetch(apt.source_url)
                    result = extract_coord_from_detail(html)
                    if result:
                        lat, lng = result
                        detail_fallback += 1
                    else:
                        logger.warning("  No coords found for %s", apt.source_id)
                        missing += 1
                        continue
                except Exception as e:
                    logger.warning("  Detail fetch failed for %s: %s", apt.source_id, e)
                    missing += 1
                    continue

            old_lat, old_lng = apt.latitude, apt.longitude
            apt.latitude  = lat
            apt.longitude = lng

            if boundary_latlngs:
                apt.within_boundary = point_in_polygon(lat, lng, boundary_latlngs)

            logger.info(
                "  %-52s  lat=%.5f lng=%.5f  within=%s  (was lat=%.5f lng=%.5f)",
                apt.address[:52], lat, lng, apt.within_boundary,
                old_lat or 0, old_lng or 0,
            )
            updated += 1

        db.session.commit()

        logger.info("\n=== Done ===")
        logger.info("  Updated:         %d", updated)
        logger.info("  Detail fallback: %d", detail_fallback)
        logger.info("  Missing:         %d", missing)

        within = Apartment.query.filter_by(within_boundary=True).count()
        outside = Apartment.query.filter_by(within_boundary=False).count()
        logger.info("  Within boundary: %d", within)
        logger.info("  Outside boundary: %d", outside)


if __name__ == "__main__":
    main()
