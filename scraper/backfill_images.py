"""
Backfill missing images for listings that only got 1-2 photos.

Fetches the detail page for each under-imaged listing, downloads any
ygl-photos or CloudFront images found, saves them to:
  static/images/apartments/          (same folder as everything else)

Updates the database in place.

Run from the project root:
    python -m scraper.backfill_images
"""

import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

from app import app, db
from models import Apartment
from scraper.metro_scraper import download_image, REQUEST_DELAY

session = cffi.Session(impersonate="chrome124")

MIN_IMAGES = 3   # listings with fewer than this will be backfilled


def fetch_image_urls(url: str) -> list[str]:
    """Fetch detail page and return all listing photo URLs."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        for img in soup.select("img[src*='ygl-photos'], img[src*='cloudfront.net']"):
            src = img.get("src", "")
            if src and src not in urls:
                urls.append(src)
        return urls
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return []


def run():
    with app.app_context():
        listings = Apartment.query.filter_by(active=True).all()
        needs_update = [
            a for a in listings
            if len(json.loads(a.images_json or "[]")) < MIN_IMAGES
        ]

        print(f"Found {len(needs_update)} listings with < {MIN_IMAGES} images\n")

        for apt in needs_update:
            current = json.loads(apt.images_json or "[]")
            print(f"[{apt.source_id}] currently {len(current)} image(s) — fetching detail page…")

            remote_urls = fetch_image_urls(apt.source_url)
            print(f"  Found {len(remote_urls)} photos on detail page")

            if not remote_urls:
                print("  Skipping — no images found on page")
                continue

            # Figure out which indices we already have so we don't re-download
            # Filenames are like  source_id_0.jpg, source_id_1.jpg …
            existing_indices = set()
            for path in current:
                fname = Path(path).stem          # e.g. "beacon-st-boston-ma-433923501_0"
                parts = fname.rsplit("_", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    existing_indices.add(int(parts[1]))

            new_paths = list(current)
            for i, img_url in enumerate(remote_urls[:12]):
                if i in existing_indices:
                    continue                      # already downloaded
                path = download_image(img_url, apt.source_id, i)
                if path and path not in new_paths:
                    new_paths.append(path)

            apt.images_json = json.dumps(new_paths)
            db.session.commit()
            print(f"  Updated: {len(current)} → {len(new_paths)} images\n")

        print("Backfill complete.")


if __name__ == "__main__":
    run()
