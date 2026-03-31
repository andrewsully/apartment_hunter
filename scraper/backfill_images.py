"""
Full image backfill — fetches every listing's detail page and downloads
any photos we're missing. Uses parallel image downloads for speed.

Run from the project root:
    python -m scraper.backfill_images
"""

import json
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

from app import app, db
from models import Apartment
from scraper.metro_scraper import IMAGE_DIR, download_image

# Polite delay between detail-page fetches (images are parallelised separately)
PAGE_DELAY   = 1.5
MAX_IMAGES   = 12   # cap per listing
IMG_WORKERS  = 8    # parallel image download threads

session = cffi.Session(impersonate="chrome124")


def fetch_remote_urls(url: str) -> list[str]:
    """Return listing photo URLs from a detail page."""
    try:
        time.sleep(PAGE_DELAY)
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


def existing_indices(paths: list[str]) -> set[int]:
    """Return the set of index numbers we already have on disk for a listing."""
    indices = set()
    for p in paths:
        fname = Path(p).stem          # e.g. "beacon-st-boston-ma-433923501_3"
        parts = fname.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            local = IMAGE_DIR / Path(p).name
            if local.exists():            # only count files that are actually there
                indices.add(int(parts[1]))
    return indices


def run():
    with app.app_context():
        listings = Apartment.query.filter_by(active=True).all()
        print(f"Checking {len(listings)} listings for missing images…\n")
        total_added = 0

        for apt in listings:
            current  = json.loads(apt.images_json or "[]")
            have_idx = existing_indices(current)

            # Fetch detail page
            remote_urls = fetch_remote_urls(apt.source_url)
            capped      = remote_urls[:MAX_IMAGES]

            # Which indices are missing?
            missing = [(i, url) for i, url in enumerate(capped)
                       if i not in have_idx]

            if not missing:
                print(f"  OK  {apt.source_id}  ({len(current)} images, nothing to add)")
                continue

            print(f"  {apt.source_id}: have {len(have_idx)}, "
                  f"remote {len(capped)}, downloading {len(missing)} missing…")

            # Parallel image downloads
            new_paths = {Path(p).stem.rsplit('_',1)[1]: p
                         for p in current
                         if len(Path(p).stem.rsplit('_',1)) == 2}

            def dl(args):
                idx, url = args
                return idx, download_image(url, apt.source_id, idx)

            with ThreadPoolExecutor(max_workers=IMG_WORKERS) as pool:
                futures = {pool.submit(dl, item): item for item in missing}
                for fut in as_completed(futures):
                    idx, path = fut.result()
                    if path:
                        new_paths[str(idx)] = path

            # Rebuild ordered list
            ordered = [new_paths[k] for k in sorted(new_paths, key=lambda x: int(x))
                       if new_paths[k]]

            apt.images_json = json.dumps(ordered)
            db.session.commit()
            added = len(ordered) - len(current)
            total_added += added
            print(f"    → {len(current)} → {len(ordered)} images  (+{added})")

        print(f"\nDone. Total new images downloaded: {total_added}")


if __name__ == "__main__":
    run()
