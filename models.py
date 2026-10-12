from datetime import date, datetime, timezone

from flask import abort
from flask_login import UserMixin
from google.cloud.firestore_v1 import transactional
from google.cloud.firestore_v1.base_query import FieldFilter
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import get_db


@transactional
def _bump_counter(transaction, ref):
    snap = ref.get(transaction=transaction)
    value = (snap.get("value") if snap.exists else 0) + 1
    transaction.set(ref, {"value": value})
    return value


def _to_firestore(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


class Model:
    """
    Minimal Firestore document mapper. Each subclass is one collection; the
    document ID is the integer primary key (as a string), allocated from a
    per-collection counter so URLs and displayed IDs stay short and readable.

    Only equality filters (and range filters on a single field) are sent to
    Firestore, so no composite indexes are needed. Sorting and cross-field
    filtering happen in Python.
    """

    collection = ""
    id_field = ""
    defaults = {}  # field name -> default value (or zero-arg callable)
    date_fields = ()  # stored in Firestore as ISO-8601 strings

    def __init__(self, **kwargs):
        setattr(self, self.id_field, kwargs.pop(self.id_field, None))
        for name, default in self.defaults.items():
            if name in kwargs:
                value = kwargs.pop(name)
            else:
                value = default() if callable(default) else default
            setattr(self, name, value)
        if kwargs:
            raise TypeError(f"Unknown fields for {type(self).__name__}: {', '.join(kwargs)}")

    # ---- serialization -------------------------------------------------
    def to_dict(self):
        data = {self.id_field: getattr(self, self.id_field)}
        for name in self.defaults:
            data[name] = _to_firestore(getattr(self, name))
        return data

    @classmethod
    def _from_snapshot(cls, snap):
        data = snap.to_dict()
        for name in cls.date_fields:
            if isinstance(data.get(name), str):
                data[name] = date.fromisoformat(data[name])
        for name, value in data.items():
            if isinstance(value, datetime) and value.tzinfo is not None:
                data[name] = value.astimezone(timezone.utc).replace(tzinfo=None)
        return cls(**data)

    # ---- persistence ---------------------------------------------------
    @classmethod
    def _col(cls):
        return get_db().collection(cls.collection)

    def save(self):
        if getattr(self, self.id_field) is None:
            db = get_db()
            counter = db.collection("counters").document(self.collection)
            setattr(self, self.id_field, _bump_counter(db.transaction(), counter))
        self._col().document(str(getattr(self, self.id_field))).set(self.to_dict())
        return self

    def delete(self):
        self._col().document(str(getattr(self, self.id_field))).delete()

    # ---- querying ------------------------------------------------------
    @classmethod
    def _query(cls, conditions, equals):
        query = cls._col()
        for field, op, value in conditions:
            query = query.where(filter=FieldFilter(field, op, _to_firestore(value)))
        for field, value in equals.items():
            query = query.where(filter=FieldFilter(field, "==", _to_firestore(value)))
        return query

    @classmethod
    def find(cls, *conditions, **equals):
        """conditions: (field, op, value) tuples; equals: field=value equality filters."""
        return [cls._from_snapshot(s) for s in cls._query(conditions, equals).stream()]

    @classmethod
    def all(cls):
        return cls.find()

    @classmethod
    def first(cls, **equals):
        for snap in cls._query((), equals).limit(1).stream():
            return cls._from_snapshot(snap)
        return None

    @classmethod
    def count(cls, **equals):
        result = cls._query((), equals).count(alias="n").get()
        return int(result[0][0].value)

    @classmethod
    def get(cls, id_):
        snap = cls._col().document(str(id_)).get()
        return cls._from_snapshot(snap) if snap.exists else None

    @classmethod
    def get_or_404(cls, id_):
        obj = cls.get(id_)
        if obj is None:
            abort(404)
        return obj

    @classmethod
    def index(cls):
        """All documents of this collection keyed by their integer ID."""
        return {getattr(o, cls.id_field): o for o in cls.all()}


class User(UserMixin, Model):
    collection = "users"
    id_field = "user_id"
    defaults = {
        "full_name": None,
        "email": None,
        "password_hash": None,
        "role": "resident",             # resident | vendor | lgu_staff | dti_da | admin
        "city": None,                    # Cebu City | Lapu-Lapu City
        "contact_number": None,
        "is_verified_vendor": False,
        "subscription_status": "trial",  # active | inactive | trial
        "is_active_flag": True,
        "created_at": datetime.utcnow,
        "address": None,
        "business_name": None,
        "business_address": None,
        "market_id": None,
        "vendor_status": "active",       # pending | active | suspended (vendors only)
        "vendor_verified_at": None,
        "org_name": None,
        "division": None,
        "contact_person": None,
        "profile_pic": None,             # NEW — path to profile picture
    }

    # Flask-Login requires get_id(); primary key column name differs from "id"
    def get_id(self):
        return str(self.user_id)

    @property
    def is_active(self):
        return self.is_active_flag

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def is_lgu_or_agency(self):
        return self.role in ("lgu_staff", "dti_da", "admin")


class Market(Model):
    collection = "markets"
    id_field = "market_id"
    defaults = {
        "market_name": None,
        "city": None,
        "address": None,       # for text-based markets
        "latitude": None,
        "longitude": None,
    }


class Product(Model):
    collection = "products"
    id_field = "product_id"
    defaults = {
        "product_name": None,
        "unit": "kg",
        "category": "General",
    }


class DtiSrp(Model):
    collection = "dti_srp"
    id_field = "srp_id"
    defaults = {
        "product_id": None,
        "srp_price": None,
        "bulletin_date": None,
        "source": "DTI SRP Bulletin",
    }
    date_fields = ("bulletin_date",)


class PriceReport(Model):
    collection = "price_reports"
    id_field = "report_id"
    defaults = {
        "user_id": None,
        "product_id": None,
        "market_id": None,
        "commodity_name": None,        # text commodity name
        "market_name": None,            # text market name
        "price": None,
        "photo_path": None,
        "report_date": None,
        "status": "pending",            # pending | verified | rejected | flagged
        "is_spike": False,
        "spike_percent": None,
        "verified_by": None,
        "verified_at": None,
        "created_at": datetime.utcnow,
        "verification_notes": None,
        "vendor_name": None,
    }
    date_fields = ("report_date",)


class Alert(Model):
    collection = "alerts"
    id_field = "alert_id"
    defaults = {
        "report_id": None,
        "message": None,
        "severity": "medium",           # low | medium | high
        "status": "open",               # open | acknowledged | escalated | resolved
        "product_name": None,
        "market_name": None,
        "reported_price": None,
        "baseline": None,
        "spike_percent": None,
        "is_resolved": False,
        "resolved_by": None,
        "created_at": datetime.utcnow,
        "resolved_at": None,
    }


class ReportExport(Model):
    collection = "report_exports"
    id_field = "export_id"
    defaults = {
        "generated_by": None,
        "file_type": None,              # pdf | csv
        "file_path": None,
        "date_from": None,
        "date_to": None,
        "created_at": datetime.utcnow,
        "title": None,
        "market_id": None,
        "size_bytes": 0,
        "shared_with_agency": False,
    }
    date_fields = ("date_from", "date_to")


class Listing(Model):
    collection = "listings"
    id_field = "listing_id"
    defaults = {
        "vendor_id": None,
        "name": None,
        "category": "General",
        "unit": "kg",
        "price": None,
        "prev_price": None,
        "stock_status": "in_stock",     # in_stock | low_stock | out_of_stock
        "product_id": None,             # optional link to a tracked commodity
        "updated_at": datetime.utcnow,
        "created_at": datetime.utcnow,
    }


class Review(Model):
    collection = "reviews"
    id_field = "review_id"
    defaults = {
        "vendor_id": None,
        "user_id": None,
        "user_name": None,
        "rating": 5,
        "comment": None,
        "reply": None,
        "is_flagged": False,
        "created_at": datetime.utcnow,
    }


class PriceAlertRule(Model):
    collection = "price_alert_rules"
    id_field = "rule_id"
    defaults = {
        "user_id": None,
        "product_id": None,
        "direction": "above",           # above | below
        "threshold": None,
        "is_triggered": False,
        "triggered_price": None,
        "triggered_at": None,
        "created_at": datetime.utcnow,
    }


class ScanEvent(Model):
    collection = "scan_events"
    id_field = "scan_id"
    defaults = {"vendor_id": None, "created_at": datetime.utcnow}


class Subscription(Model):
    collection = "subscriptions"
    id_field = "sub_id"
    defaults = {
        "user_id": None,
        "plan": "quarterly",            # monthly | quarterly | annual
        "status": "active",             # active | cancelled
        "started_at": datetime.utcnow,
        "renews_at": None,
        "payment_type": None,           # card | gcash | maya | bank
        "payment_label": None,
        "payment_last4": None,
        "payment_expiry": None,
        "qr_generated": 0,
    }


class BillingRecord(Model):
    collection = "billing_records"
    id_field = "record_id"
    defaults = {
        "user_id": None,
        "description": None,
        "amount": None,
        "status": "paid",
        "created_at": datetime.utcnow,
    }


class AuditLog(Model):
    collection = "audit_logs"
    id_field = "log_id"
    defaults = {
        "user_id": None,
        "user_name": None,
        "action": None,
        "detail": None,
        "created_at": datetime.utcnow,
    }