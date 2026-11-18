from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from flask_login import current_user, login_required

from models import Alert, Listing, Market, PriceAlertRule, PriceReport, Product, Review, User
from utils.helpers import (
    market_price_board, refresh_alert_rules, role_required, vendor_stats,
)
from utils.spike_detection import create_report
from utils import file_store
from utils.notifications import notify

bp = Blueprint("resident", __name__)

CITIES = ("Cebu City", "Lapu-Lapu City")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
resident_only = role_required("resident")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _registered_vendors():
    """Vendors a resident can pick: every registered vendor account except suspended ones."""
    markets = Market.index()
    vendors = [v for v in User.find(role="vendor") if v.vendor_status != "suspended"]
    vendors.sort(key=lambda v: (v.business_name or v.full_name or "").lower())
    return vendors, markets


def _vendor_choices():
    vendors, markets = _registered_vendors()
    choices = []
    for v in vendors:
        market = markets.get(v.market_id)
        place = ", ".join(x for x in (market.market_name if market else None, v.city) if x)
        choices.append({"id": v.user_id, "name": v.business_name or v.full_name or f"Vendor #{v.user_id}",
                        "place": place, "market": market.market_name if market else ""})
    return choices


def _parse_date(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def report_accuracy(user_id):
    stats = [r.status for r in PriceReport.find(user_id=user_id)]
    verified = stats.count("verified")
    judged = verified + sum(1 for s in stats if s in ("rejected", "flagged"))
    return round(5 * verified / judged, 1) if judged else None


@bp.route("/dashboard")
@resident_only
def dashboard():
    rules = refresh_alert_rules(current_user.user_id)
    products = Product.index()
    markets = Market.index()
    my_reports = PriceReport.find(user_id=current_user.user_id)
    my_reviews = Review.find(user_id=current_user.user_id)

    since = date.today() - timedelta(days=7)
    changes = sum(
        1 for r in PriceReport.find(status="verified")
        if r.report_date >= since
        and (not current_user.city or (markets.get(r.market_id) and markets[r.market_id].city == current_user.city))
    )

    # ---- Build recent activity feed ----
    events = []

    for r in my_reports:
        name = r.commodity_name if r.commodity_name else (
            products[r.product_id].product_name if r.product_id in products else "a commodity"
        )
        key = f"report:{r.report_id}:{r.status}"
        if r.status == "verified" and r.verified_at:
            events.append(("check", f"Your report on {name} was verified", r.verified_at, key))
        elif r.status == "rejected" and r.verified_at:
            events.append(("xmark", f"Your report on {name} was rejected", r.verified_at, key))
        elif r.status == "pending" and r.created_at:
            events.append(("clock", f"You submitted a price report for {name}", r.created_at, key))
        elif r.status == "flagged" and r.created_at:
            events.append(("alert", f"Your report on {name} was flagged for review", r.created_at, key))

    vendor_cache = {}
    for rev in my_reviews:
        if rev.vendor_id not in vendor_cache:
            vendor_cache[rev.vendor_id] = User.get(rev.vendor_id)
        vendor = vendor_cache[rev.vendor_id]
        vendor_name = (vendor.business_name or vendor.full_name) if vendor else "a vendor"
        events.append((
            "star",
            f"You rated {vendor_name} — {rev.rating} star{'s' if rev.rating != 1 else ''}",
            rev.created_at,
            f"review:{rev.review_id}",
        ))

    for rule in rules:
        if rule.is_triggered and rule.triggered_at and rule.product_id in products:
            word = "below" if rule.direction == "below" else "above"
            events.append((
                "alert",
                f"{products[rule.product_id].product_name} {word} ₱{rule.threshold:g}/kg — now ₱{rule.triggered_price:.2f}",
                rule.triggered_at,
                f"rule:{rule.rule_id}:{rule.triggered_at:%Y%m%d%H%M%S}",
            ))

    for v in User.find(role="vendor", is_verified_vendor=True):
        if v.vendor_verified_at and (not current_user.city or v.city == current_user.city):
            events.append((
                "check",
                f"New verified vendor near you: {v.business_name or v.full_name}",
                v.vendor_verified_at,
                f"vendor:{v.user_id}",
            ))

    cleared_at = current_user.activity_cleared_at
    hidden = set(current_user.hidden_activity or [])
    events = [e for e in events if e[3] not in hidden and not (cleared_at and e[2] <= cleared_at)]
    events.sort(key=lambda e: e[2], reverse=True)

    return render_template(
        "resident_dashboard.html",
        report_count=len(my_reports),
        alert_count=sum(1 for r in rules if r.is_triggered),
        changes=changes,
        board=market_price_board(current_user.city),
        events=events[:5],
        vendor_choices=_vendor_choices(),
    )


MAX_HIDDEN_ACTIVITY = 100


@bp.route("/activity/dismiss", methods=["POST"])
@resident_only
def dismiss_activity():
    key = request.form.get("key", "").strip()[:80]
    if key:
        hidden = [k for k in (current_user.hidden_activity or []) if k != key]
        hidden.append(key)
        current_user.hidden_activity = hidden[-MAX_HIDDEN_ACTIVITY:]
        current_user.save()
    return redirect(url_for("resident.dashboard") + "#recent-activity")


@bp.route("/activity/clear", methods=["POST"])
@resident_only
def clear_activity():
    current_user.activity_cleared_at = datetime.utcnow()
    current_user.hidden_activity = []
    current_user.save()
    flash("Recent activity cleared.", "success")
    return redirect(url_for("resident.dashboard") + "#recent-activity")


@bp.route("/report/new", methods=["GET", "POST"])
@resident_only
def report_price():
    vendors, markets = _registered_vendors()

    if request.method == "POST":
        back = redirect(url_for("resident.report_price"))
        market = markets.get(request.form.get("market_id", type=int))
        vendor = next((v for v in vendors if v.user_id == request.form.get("vendor_id", type=int)), None)
        listing = Listing.get(request.form.get("listing_id", type=int) or 0)
        price = request.form.get("price", type=float)

        if market is None:
            flash("Please choose a market from the list.", "danger")
            return back
        if vendor is None or vendor.market_id != market.market_id:
            flash("Please choose a registered vendor in that market.", "danger")
            return back
        if listing is None or listing.vendor_id != vendor.user_id:
            flash("Please choose one of that vendor's commodities.", "danger")
            return back
        if price is None or not price > 0:
            flash("Enter a valid price per unit.", "danger")
            return back
        try:
            report_date = _parse_date(request.form.get("report_date") or str(date.today()))
        except ValueError:
            flash("Invalid date.", "danger")
            return back
        if report_date > date.today():
            flash("The report date can't be in the future.", "danger")
            return back

        # Reports are tracked per commodity: use the listing's linked commodity, else one with the same name.
        product = Product.get(listing.product_id) if listing.product_id else None
        if product is None:
            product = next((p for p in Product.all() if p.product_name.lower() == listing.name.lower()), None)
        if product is None:
            product = Product(product_name=listing.name, unit=listing.unit or "kg",
                              category=(listing.category or "others").lower()).save()

        photo = request.files.get("photo")
        photo_path = None
        if photo and photo.filename and allowed_file(photo.filename):
            try:
                photo_path = file_store.save_photo(photo, owner_id=current_user.user_id, kind="report_photo")
            except file_store.InvalidImage:
                flash("The photo couldn't be read, so the report was sent without it.", "warning")

        _, is_spike = create_report(
            current_user.user_id, product.product_id, market.market_id, price,
            report_date, photo_path, vendor.business_name or vendor.full_name,
        )
        if is_spike:
            flash(
                "Your report was submitted and automatically flagged for LGU review "
                "because the price is significantly above normal.",
                "warning",
            )
        else:
            flash(f"Price report for {listing.name} submitted. It is now pending LGU verification.", "success")
        return back

    # Form options: markets by city -> vendors in each market -> each vendor's commodities.
    listings_by_vendor = {}
    for l in Listing.all():
        listings_by_vendor.setdefault(l.vendor_id, []).append(l)
    vendor_options = [
        {"id": v.user_id, "market_id": v.market_id, "name": v.business_name or v.full_name or f"Vendor #{v.user_id}",
         "items": [{"id": l.listing_id, "name": l.name, "unit": l.unit or "unit", "price": l.price}
                   for l in sorted(listings_by_vendor.get(v.user_id, []), key=lambda l: (l.name or "").lower())]}
        for v in vendors if v.market_id in markets
    ]
    city_order = {c: i for i, c in enumerate(CITIES)}
    markets_by_city = {}
    for m in sorted(markets.values(), key=lambda m: (city_order.get(m.city, 99), m.city or "", m.market_name.lower())):
        markets_by_city.setdefault(m.city or "Other", []).append(m)

    products = sorted(Product.all(), key=lambda p: p.product_name)
    recent = sorted(
        PriceReport.find(user_id=current_user.user_id),
        key=lambda r: r.created_at,
        reverse=True
    )[:5]
    product_map = {p.product_id: p for p in products}
    market_map = {m.market_id: m for m in Market.all()}
    for r in recent:
        r.product = product_map.get(r.product_id)
        r.market = market_map.get(r.market_id)
    return render_template(
        "report_price.html", today=date.today(), recent=recent,
        markets_by_city=markets_by_city, vendor_options=vendor_options,
    )


@bp.route("/alerts", methods=["GET", "POST"])
@resident_only
def alerts():
    products = sorted(Product.all(), key=lambda p: p.product_name)
    product_map = {p.product_id: p for p in products}

    if request.method == "POST":
        commodity_name = request.form.get("commodity_name", "").strip()
        threshold = request.form.get("threshold", type=float)
        direction = request.form.get("direction", "above")

        if not commodity_name or threshold is None or not threshold > 0 or direction not in ("above", "below"):
            flash("Enter a commodity and a valid price threshold.", "danger")
            return redirect(url_for("resident.alerts"))

        product = next(
            (p for p in products if p.product_name.lower() == commodity_name.lower()),
            None
        )
        if product is None:
            product = Product(
                product_name=commodity_name,
                unit="kg",
                category="others"
            )
            product.save()
            products = sorted(Product.all(), key=lambda p: p.product_name)

        PriceAlertRule(
            user_id=current_user.user_id,
            product_id=product.product_id,
            direction=direction,
            threshold=threshold,
        ).save()
        flash("Price alert saved.", "success")
        return redirect(url_for("resident.alerts"))

    rules = sorted(refresh_alert_rules(current_user.user_id), key=lambda r: r.created_at, reverse=True)
    for r in rules:
        r.product = product_map.get(r.product_id)

    markets = Market.index()
    spike = None
    for a in sorted(Alert.all(), key=lambda a: a.created_at, reverse=True):
        report = PriceReport.get(a.report_id)
        market = markets.get(report.market_id) if report else None
        if market and (not current_user.city or market.city == current_user.city):
            spike = a
            break

    return render_template("resident_alerts.html", products=products, rules=rules, spike=spike)


@bp.route("/alerts/<int:rule_id>/delete", methods=["POST"])
@resident_only
def delete_rule(rule_id):
    rule = PriceAlertRule.get_or_404(rule_id)
    if rule.user_id == current_user.user_id:
        rule.delete()
        flash("Alert removed.", "info")
    return redirect(url_for("resident.alerts"))


@bp.route("/profile")
@resident_only
def profile():
    rules = refresh_alert_rules(current_user.user_id)
    return render_template(
        "resident_profile.html",
        report_count=PriceReport.count(user_id=current_user.user_id),
        alert_count=sum(1 for r in rules if r.is_triggered),
        accuracy=report_accuracy(current_user.user_id),
    )


@bp.route("/profile/upload-picture", methods=["POST"])
@resident_only
def upload_profile_picture():
    photo = request.files.get("profile_pic")
    if not photo or not photo.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("resident.profile"))

    if not allowed_file(photo.filename):
        flash("Only PNG, JPG, and JPEG files are allowed.", "danger")
        return redirect(url_for("resident.profile"))

    try:
        ref = file_store.save_photo(photo, owner_id=current_user.user_id, kind="profile_pic", max_side=512)
    except file_store.InvalidImage:
        flash("That file isn't a valid image. Please choose a PNG or JPG photo.", "danger")
        return redirect(url_for("resident.profile"))

    old = current_user.profile_pic
    current_user.profile_pic = ref
    current_user.save()
    if file_store.is_stored(old):
        file_store.delete(old)  # don't keep replaced profile pictures around

    flash("Profile picture updated successfully!", "success")
    return redirect(url_for("resident.profile"))


@bp.route("/scan")
@resident_only
def scan_qr():
    return render_template("scan.html")


@bp.route("/feedback/submit", methods=["POST"])
@resident_only
def submit_feedback():
    vendor_id = request.form.get("vendor_id", type=int)
    rating = request.form.get("rating", type=int)
    comment = request.form.get("comment", "").strip()[:500]

    if not vendor_id or rating is None or rating < 1 or rating > 5:
        flash("Please select a vendor and a rating between 1 and 5.", "danger")
        return redirect(url_for("resident.dashboard"))

    vendor = User.get(vendor_id)
    if not vendor or vendor.role != "vendor" or vendor.vendor_status == "suspended":
        flash("Please choose a registered vendor from the list.", "danger")
        return redirect(url_for("resident.dashboard"))

    Review(
        vendor_id=vendor_id,
        user_id=current_user.user_id,
        user_name=current_user.full_name or "Anonymous",
        rating=rating,
        comment=comment or None,
    ).save()
    who = current_user.full_name or "A shopper"
    notify(vendor_id, f"New {rating}-star review", f"{who}: {comment[:120]}" if comment else f"From {who}",
           link="/vendor/feedback", icon="star", tone="good" if rating >= 4 else ("warn" if rating == 3 else "bad"))

    flash(f"Thanks! Your feedback for {vendor.business_name or vendor.full_name} has been sent.", "success")
    return redirect(url_for("resident.dashboard"))


# ---- Allows Vendors to view charts too ----
@bp.route("/prices/history")
@login_required
def price_history():
    product_id = request.args.get("product_id", type=int)
    products = sorted(Product.all(), key=lambda p: p.product_name)
    return render_template("price_history.html", products=products, selected_product=product_id)


# ---- CHANGED: Allows Vendors to fetch chart data too ----
@bp.route("/api/prices/history-data")
@login_required
def price_history_data():
    product_id = request.args.get("product_id", type=int)
    days = request.args.get("days", 30, type=int)
    since = date.today() - timedelta(days=days)

    equals = {"status": "verified"}
    if product_id:
        equals["product_id"] = product_id

    prices_by_day = defaultdict(list)
    for r in PriceReport.find(**equals):
        if r.report_date >= since:
            prices_by_day[r.report_date].append(float(r.price))

    days_sorted = sorted(prices_by_day)
    return jsonify({
        "labels": [str(d) for d in days_sorted],
        "values": [round(sum(prices_by_day[d]) / len(prices_by_day[d]), 2) for d in days_sorted],
    })