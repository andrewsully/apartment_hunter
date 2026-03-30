import os
import json
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from models import db, Apartment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "apartments.db")
os.makedirs(os.path.dirname(_db_path), exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

db.init_app(app)

BOUNDARY_FILE = os.path.join(os.path.dirname(__file__), "data", "boundary.json")


def load_boundary():
    if os.path.exists(BOUNDARY_FILE):
        with open(BOUNDARY_FILE) as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Main apartment browser — loads with boundary applied."""
    boundary = load_boundary()
    total = Apartment.query.count()
    active = Apartment.query.filter_by(active=True, within_boundary=True).count()
    last = Apartment.query.order_by(Apartment.scraped_at.desc()).first()
    last_scraped = last.scraped_at.isoformat() if last else None
    return render_template(
        "index.html",
        boundary=json.dumps(boundary),
        total_apartments=active,
        last_scraped=last_scraped,
    )


@app.route("/admin")
def admin():
    """Admin panel: boundary editor + scrape controls. Not linked from main UI."""
    boundary = load_boundary()
    return render_template("admin.html", boundary=json.dumps(boundary))


# ---------------------------------------------------------------------------
# Boundary API
# ---------------------------------------------------------------------------

@app.route("/api/boundary", methods=["GET"])
def get_boundary():
    boundary = load_boundary()
    return jsonify(boundary)


@app.route("/api/boundary", methods=["POST"])
def save_boundary():
    data = request.get_json()
    if not data or "latlngs" not in data:
        return jsonify({"error": "Invalid boundary data"}), 400
    os.makedirs("data", exist_ok=True)
    data["updated_at"] = datetime.utcnow().isoformat()
    with open(BOUNDARY_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Boundary saved with %d points", len(data["latlngs"]))
    return jsonify({"success": True, "points": len(data["latlngs"])})


# ---------------------------------------------------------------------------
# Apartments API
# ---------------------------------------------------------------------------

@app.route("/api/apartments", methods=["GET"])
def get_apartments():
    apartments = Apartment.query.filter_by(active=True).all()
    return jsonify([a.to_dict() for a in apartments])


@app.route("/api/apartments/<int:apt_id>/rank", methods=["POST"])
def rank_apartment(apt_id):
    data = request.get_json()
    apt = Apartment.query.get_or_404(apt_id)
    if "rank" in data:
        apt.rank = data["rank"]
    if "list_category" in data:
        apt.list_category = data["list_category"]
    if "notes" in data:
        apt.notes = data["notes"]
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Static images (apartments stored locally)
# ---------------------------------------------------------------------------

@app.route("/images/apartments/<path:filename>")
def apartment_image(filename):
    return send_from_directory(
        os.path.join(app.root_path, "static", "images", "apartments"), filename
    )


# ---------------------------------------------------------------------------
# Scrape trigger (manual, for safety)
# ---------------------------------------------------------------------------

@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    """Manually trigger a scrape. Protect this in production."""
    try:
        from scraper.metro_scraper import run_scrape
        boundary = load_boundary()
        count = run_scrape(boundary)
        return jsonify({"success": True, "new_listings": count})
    except Exception as e:
        logger.error("Scrape failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape/status", methods=["GET"])
def scrape_status():
    total = Apartment.query.count()
    active = Apartment.query.filter_by(active=True).count()
    last = Apartment.query.order_by(Apartment.scraped_at.desc()).first()
    return jsonify({
        "total": total,
        "active": active,
        "last_scraped": last.scraped_at.isoformat() if last else None,
    })


if __name__ == "__main__":
    with app.app_context():
        os.makedirs("data", exist_ok=True)
        db.create_all()
    app.run(debug=True, port=5001)
