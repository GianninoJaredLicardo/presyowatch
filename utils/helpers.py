"""Shared helpers: settings, audit log, auth decorators, formatting, price boards."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from config import Config
from extensions import get_db
from models import (
    AuditLog, DtiSrp, Listing, Market, PriceAlertRule, PriceReport, Product, Review,
)

PHT_OFFSET = timedelta(hours=8)  # Philippine Standard Time


# ---- auth ---------------------------------------------------------------
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def home_endpoint_for(user):
    return {
        "admin": "admin.dashboard",
        "lgu_staff": "lgu.dashboard",
        "dti_da": "lgu.dashboard",
        "vendor": "vendor.dashboard",
    }.get(user.role, "resident.dashboard")


# ---- system settings ------------------------------------------------------
def get_settings():
    snap = get_db().collection("settings").document("system").get()
    data = snap.to_dict() if snap.exists else {}
    return {
        "spike_threshold": float(data.get("spike_threshold", Config.SPIKE_THRESHOLD_PERCENT)),
        "email_notifications": bool(data.get("email_notifications", True)),
        "sms_alerts": bool(data.get("sms_alerts", False)),
    }


def save_settings(**values):
    get_db().collection("settings").document("system").set(values, merge=True)


# ---- audit log ------------------------------------------------------------
def log_action(user, action, detail=""):
    AuditLog(
        user_id=getattr(user, "user_id", None),
        user_name=getattr(user, "full_name", None),
        action=action,
        detail=detail,
    ).save()


# ---- formatting -----------------------------------------------------------
def timeago(value):
    if not value:
        return ""
    if not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    secs = int((datetime.utcnow() - value).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 7 * 86400:
        return f"{secs // 86400}d ago"
    return (value + PHT_OFFSET).strftime("%b %d")


def localtime(value, fmt="%b %d, %I:%M %p"):
    if not value:
        return ""
    if not isinstance(value, datetime):
        return value.strftime("%b %d")
    return (value + PHT_OFFSET).strftime(fmt)


def peso(value):
    return f"{float(value):,.2f}"


# ---- vendor / price data ---------------------------------------------------
def vendor_stats(vendor_id):
    reviews = Review.find(vendor_id=vendor_id)
    rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else None

    reports = PriceReport.find(user_id=vendor_id)
    verified = sum(1 for r in reports if r.status == "verified")
    judged = verified + sum(1 for r in reports if r.status in ("rejected", "flagged"))
    accuracy = round(100 * verified / judged) if judged else None
    return {"rating": rating, "review_count": len(reviews), "accuracy": accuracy}


def latest_srp_by_product():
    latest = {}
    for s in DtiSrp.all():
        cur = latest.get(s.product_id)
        if cur is None or (s.bulletin_date, s.srp_id) > (cur.bulletin_date, cur.srp_id):
            latest[s.product_id] = s
    return latest


def market_price_board(city=None, limit=5):
    """Latest verified average price per commodity, with direction vs. the previous day (or SRP)."""
    products = Product.all()
    srp = latest_srp_by_product()
    markets = Market.index()

    by_day = defaultdict(lambda: defaultdict(list))
    for r in PriceReport.find(status="verified"):
        market = markets.get(r.market_id)
        if city and (market is None or market.city != city):
            continue
        by_day[r.product_id][r.report_date].append(float(r.price))

    rows = []
    for p in products:
        days = sorted(by_day.get(p.product_id, {}))
        srp_price = float(srp[p.product_id].srp_price) if p.product_id in srp else None
        if days:
            cur = sum(by_day[p.product_id][days[-1]]) / len(by_day[p.product_id][days[-1]])
            if len(days) > 1:
                prev_prices = by_day[p.product_id][days[-2]]
                prev = sum(prev_prices) / len(prev_prices)
            else:
                prev = srp_price
            has_data = True
        elif srp_price is not None:
            cur, prev, has_data = srp_price, None, False
        else:
            continue
        direction = None
        if prev is not None and cur != prev:
            direction = "up" if cur > prev else "down"
        rows.append(
            {"name": p.product_name, "unit": p.unit, "price": cur,
             "direction": direction, "has_data": has_data}
        )
    rows.sort(key=lambda r: (not r["has_data"], r["name"]))
    return rows[:limit]


def refresh_alert_rules(user_id):
    """Re-evaluate a resident's price alert rules against current verified prices and vendor listings."""
    rules = PriceAlertRule.find(user_id=user_id)
    since = date.today() - timedelta(days=14)
    for rule in rules:
        prices = [
            float(r.price)
            for r in PriceReport.find(product_id=rule.product_id, status="verified")
            if r.report_date >= since
        ]
        prices += [float(l.price) for l in Listing.find(product_id=rule.product_id)
                   if l.stock_status != "out_of_stock"]
        hit_price = None
        if prices:
            if rule.direction == "below" and min(prices) <= rule.threshold:
                hit_price = min(prices)
            elif rule.direction == "above" and max(prices) >= rule.threshold:
                hit_price = max(prices)
        triggered = hit_price is not None
        if triggered != rule.is_triggered:
            rule.is_triggered = triggered
            rule.triggered_price = hit_price
            rule.triggered_at = datetime.utcnow() if triggered else None
            rule.save()
    return rules
