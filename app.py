import os
import json
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from models import db, Apartment, UserRating, VALID_USERS

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


@app.route("/compare")
def compare():
    """Group comparison page — all apartments with all users' votes."""
    boundary = load_boundary()
    apartments = Apartment.query.filter_by(active=True).all()
    return render_template(
        "compare.html",
        apartments=json.dumps([a.to_dict() for a in apartments]),
        boundary=json.dumps(boundary),
    )


@app.route("/apartment/<int:apt_id>")
def apartment_detail(apt_id):
    """Full detail page for a single apartment."""
    apt = Apartment.query.get_or_404(apt_id)
    boundary = load_boundary()

    # Sorted list of active IDs for prev/next navigation
    all_ids = [a.id for a in Apartment.query.filter_by(active=True).order_by(Apartment.rent.asc()).all()]
    try:
        idx = all_ids.index(apt_id)
        prev_id = all_ids[idx - 1] if idx > 0 else None
        next_id = all_ids[idx + 1] if idx < len(all_ids) - 1 else None
    except ValueError:
        prev_id = next_id = None

    return render_template(
        "apartment.html",
        apt=apt.to_dict(),
        prev_id=prev_id,
        next_id=next_id,
        boundary=json.dumps(boundary),
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
    user = data.get("user", "").lower().strip()

    if user and user in VALID_USERS:
        # Per-user rating
        rating = UserRating.query.filter_by(apartment_id=apt_id, user=user).first()
        if not rating:
            rating = UserRating(apartment_id=apt_id, user=user)
            db.session.add(rating)
        if "list_category" in data:
            rating.list_category = data["list_category"]
        if "notes" in data:
            rating.notes = data["notes"]
    else:
        # Legacy fallback (no user supplied)
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


@app.route("/api/ratings/reset", methods=["POST"])
def reset_all_ratings():
    """Wipe every UserRating row and reset legacy fields on all apartments."""
    data = request.get_json(silent=True) or {}
    user = data.get("user", "").lower().strip()

    if user and user in VALID_USERS:
        # Clear a single user's ratings
        deleted = UserRating.query.filter_by(user=user).delete()
        if user == "andrew":
            for apt in Apartment.query.all():
                apt.list_category = "unsorted"
                apt.notes = ""
    else:
        # Wipe everyone
        deleted = UserRating.query.delete()
        for apt in Apartment.query.all():
            apt.list_category = "unsorted"
            apt.notes = ""

    db.session.commit()
    logger.info("Ratings reset: user=%r rows_deleted=%d", user or "all", deleted)
    return jsonify({"success": True, "deleted": deleted})


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


def migrate_legacy_votes():
    """One-time: move apartment.list_category / notes → andrew's UserRating row."""
    migrated = 0
    for apt in Apartment.query.all():
        legacy_cat = apt.list_category
        if legacy_cat and legacy_cat != "unsorted":
            exists = UserRating.query.filter_by(apartment_id=apt.id, user="andrew").first()
            if not exists:
                rating = UserRating(
                    apartment_id=apt.id,
                    user="andrew",
                    list_category=legacy_cat,
                    notes=apt.notes or "",
                )
                db.session.add(rating)
                migrated += 1
    if migrated:
        db.session.commit()
        logger.info("Migrated %d legacy votes → andrew's UserRating rows", migrated)


if __name__ == "__main__":
    with app.app_context():
        os.makedirs("data", exist_ok=True)
        db.create_all()
        migrate_legacy_votes()
    port = int(os.getenv("PORT", "5001"))
    app.run(debug=True, port=port)
