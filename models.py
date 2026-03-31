from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

VALID_USERS = {"andrew", "liam", "joe"}


class UserRating(db.Model):
    __tablename__ = "user_ratings"

    id = db.Column(db.Integer, primary_key=True)
    apartment_id = db.Column(db.Integer, db.ForeignKey("apartments.id"), nullable=False)
    user = db.Column(db.String(50), nullable=False)
    list_category = db.Column(db.String(50), default="unsorted")
    notes = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("apartment_id", "user", name="uq_apt_user"),)


class Apartment(db.Model):
    __tablename__ = "apartments"

    id = db.Column(db.Integer, primary_key=True)

    # Source identifiers
    source_id = db.Column(db.String(100), unique=True, nullable=False)
    source_url = db.Column(db.Text)

    # Listing details
    address = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100), default="Boston")
    state = db.Column(db.String(10), default="MA")
    zip_code = db.Column(db.String(10))
    neighborhood = db.Column(db.String(100))

    # Unit details
    bedrooms = db.Column(db.Float)
    bathrooms = db.Column(db.Float)
    sqft = db.Column(db.Integer)
    rent = db.Column(db.Integer)  # monthly rent in dollars

    # Availability
    available_from = db.Column(db.String(20))
    available_to = db.Column(db.String(20))
    no_fee = db.Column(db.Boolean, default=False)
    pet_friendly = db.Column(db.Boolean, default=False)

    # Geography (for boundary filtering)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    within_boundary = db.Column(db.Boolean, default=None)

    # Images — stored as JSON list of local paths
    images_json = db.Column(db.Text, default="[]")

    # Legacy single-user fields (kept for backward compat; prefer user_ratings)
    list_category = db.Column(db.String(50), default="unsorted")
    notes = db.Column(db.Text, default="")

    # Meta
    active = db.Column(db.Boolean, default=True)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Per-user ratings relationship
    user_ratings = db.relationship(
        "UserRating", backref="apartment", lazy="joined",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        import json

        # Build per-user ratings dict
        ratings = {}
        for r in self.user_ratings:
            ratings[r.user] = {
                "list_category": r.list_category or "unsorted",
                "notes": r.notes or "",
            }

        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "address": self.address,
            "neighborhood": self.neighborhood,
            "zip_code": self.zip_code,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "sqft": self.sqft,
            "rent": self.rent,
            "available_from": self.available_from,
            "available_to": self.available_to,
            "no_fee": self.no_fee,
            "pet_friendly": self.pet_friendly,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "within_boundary": self.within_boundary,
            "images": json.loads(self.images_json or "[]"),
            # user_ratings keyed by username
            "user_ratings": ratings,
            # Legacy top-level fields — derived from Andrew's rating for backward compat
            "list_category": ratings.get("andrew", {}).get("list_category", self.list_category or "unsorted"),
            "notes": ratings.get("andrew", {}).get("notes", self.notes or ""),
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }
