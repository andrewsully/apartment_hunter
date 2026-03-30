# Apartment Hunter 🏠

A collaborative apartment-hunting web app for a group of 3 friends looking for
3–4 bedroom units in **Back Bay, Fenway, and South End**, Boston.

Built with Flask + SQLite + Leaflet.js. Deployable on PythonAnywhere.

---

## Project Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Done | **Boundary Designer** — draw & save the geographic filter on a map |
| 2 | 🔜 Next | **Scraper** — pull listings from Metro Realty, store locally |
| 3 | 🔜 | **Apartment Browser** — view filtered listings on a map + list |
| 4 | 🔜 | **Ranking UI** — drag-to-list (Yes / Maybe / No), notes, collaborative |

---

## Quick Start (local)

```bash
# 1. Create & activate virtualenv
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

Open http://localhost:5000 — you'll see the Boundary Designer with the default
polygon for Back Bay / Fenway / South End pre-loaded.

---

## Boundary Designer (Phase 1)

- The default polygon covers the three target neighborhoods.
- Use the **Edit** tool (pencil) to drag vertices and refine the boundary.
- Click **Save Boundary** — the polygon is stored in `data/boundary.json`.
- The saved boundary is used by the scraper to filter listings by coordinates.

---

## Scraper (Phase 2 — use sparingly)

The scraper lives in `scraper/metro_scraper.py`. It:

1. Fetches the Metro Realty search results page (paginated).
2. Parses each listing card for address, rent, beds, baths.
3. Fetches each detail page for images and availability.
4. **Geocodes** the address via OpenStreetMap Nominatim (free, no API key).
5. Runs a **point-in-polygon** check against the saved boundary.
6. Downloads and stores all images locally under `static/images/apartments/`.
7. Saves everything to `data/apartments.db` (SQLite).

**Run it manually** (once a day at most — be polite to their server):

```bash
# From project root with venv active:
python -m scraper.metro_scraper

# Or via the web UI (POST to /api/scrape — protect in production):
curl -X POST http://localhost:5000/api/scrape
```

---

## Deployment — PythonAnywhere

1. Upload this repo (or `git clone` it on PythonAnywhere).
2. Create a virtualenv and `pip install -r requirements.txt`.
3. In the **Web** tab, set the WSGI file to `wsgi.py`.
4. Set static file mapping: `/static/` → `/home/<user>/apartment_hunter/static/`.
5. Reload the app.

---

## Project Structure

```
apartment_hunter/
├── app.py                  # Flask routes
├── models.py               # SQLAlchemy Apartment model
├── wsgi.py                 # PythonAnywhere WSGI entry
├── scraper/
│   └── metro_scraper.py    # Metro Realty scraper
├── templates/
│   └── index.html          # Boundary designer
├── static/
│   ├── css/
│   ├── js/
│   └── images/apartments/  # Downloaded listing photos (gitignored)
├── data/
│   ├── boundary.json       # Saved boundary polygon
│   └── apartments.db       # SQLite database (gitignored)
├── requirements.txt
└── wsgi.py
```

---

## Search Parameters (Metro Realty)

- **Neighborhoods:** Back Bay, Fenway, South End
- **Rent:** $4,000 – $6,000/month
- **Beds:** 3 or 4
- **Available:** Aug 16 – Sep 5, 2026
- [View live search](https://metrorealtycorp.com/rentals/?city_neighborhood=Boston%3ABack+Bay%2CBoston%3AFenway%2CBoston%3ASouth+End&min_rent=4000&max_rent=6000&beds%5B3%5D=3&beds%5B4%5D=4&avail_from=08%2F16%2F26&avail_to=09%2F05%2F26&sort_name=rent&sort_dir=asc)
