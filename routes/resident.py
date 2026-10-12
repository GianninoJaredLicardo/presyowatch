import os
import uuid
from collections import defaultdict
from datetime import date, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from models import Alert, Market, PriceAlertRule, PriceReport, Product, User
from utils.helpers import (
    market_price_board, refresh_alert_rules, role_required, vendor_stats,
)
from utils.spike_detection import create_report

bp = Blueprint("resident", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
resident_only = role_required("resident")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _parse_date(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def report_accuracy(user_id):
    """Share of the user's reviewed reports that were verified, on a 5-point scale."""
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
        if r.status == "verified" and r.verified_at:
            events.append(("check", f"Your report on {name} was verified", r.verified_at))
        elif r.status == "rejected" and r.verified_at:
            events.append(("xmark", f"Your report on {name} was rejected", r.verified_at))
        elif r.status == "pending" and r.created_at:
            events.append(("clock", f"You submitted a price report for {name}", r.created_at))
        elif r.status == "flagged" and r.created_at:
            events.append(("alert", f"Your report on {name} was flagged for review", r.created_at))

    for rule in rules:
        if rule.is_triggered and rule.product_id in products:
            word = "below" if rule.direction == "below" else "above"
            events.append((
                "alert",
                f"{products[rule.product_id].product_name} {word} ₱{rule.threshold:g}/kg — now ₱{rule.triggered_price:.2f}",
                rule.triggered_at,
            ))

    for v in User.find(role="vendor", is_verified_vendor=True):
        if v.vendor_verified_at and (not current_user.city or v.city == current_user.city):
            events.append((
                "check",
                f"New verified vendor near you: {v.business_name or v.full_name}",
                v.vendor_verified_at,
            ))

    events.sort(key=lambda e: e[2], reverse=True)

    return render_template(
        "resident_dashboard.html",
        report_count=len(my_reports),
        alert_count=sum(1 for r in rules if r.is_triggered),
        changes=changes,
        board=market_price_board(current_user.city),
        events=events[:5],
    )


@bp.route("/report/new", methods=["GET", "POST"])
@resident_only
def report_price():
    products = sorted(Product.all(), key=lambda p: p.product_name)

    if request.method == "POST":
        commodity_name = request.form.get("commodity_name", "").strip()
        market_name = request.form.get("market_name", "").strip()
        price = request.form.get("price", type=float)
        vendor_name = request.form.get("vendor_name", "").strip() or None
        try:
            report_date = _parse_date(request.form.get("report_date") or str(date.today()))
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(url_for("resident.report_price"))
        photo = request.files.get("photo")

        if not commodity_name or not market_name or price is None or not price > 0:
            flash("Commodity, market, and price are required.", "danger")
            return redirect(url_for("resident.report_price"))

        product = next(
            (p for p in products if p.product_name.lower() == commodity_name.lower()),
            None
        )
        if product is None:
            product = Product(product_name=commodity_name, unit="kg", category="others")
            product.save()
            products = sorted(Product.all(), key=lambda p: p.product_name)

        market = next(
            (m for m in Market.all() if m.market_name.lower() == market_name.lower()),
            None
        )
        if market is None:
            market = Market(
                market_name=market_name,
                city=current_user.city or "Cebu City",
                address=market_name
            )
            market.save()

        product_id = product.product_id
        market_id = market.market_id

        photo_path = None
        if photo and photo.filename and allowed_file(photo.filename):
            ext = photo.filename.rsplit(".", 1)[1].lower()
            filename = secure_filename(f"{uuid.uuid4().hex}.{ext}")
            os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
            photo.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
            photo_path = filename

        _, is_spike = create_report(
            current_user.user_id, product_id, market_id, price,
            report_date, photo_path, vendor_name
        )
        if is_spike:
            flash(
                "Your report was submitted and automatically flagged for LGU review "
                "because the price is significantly above normal.",
                "warning",
            )
        else:
            flash("Price report submitted. It is now pending LGU verification.", "success")
        return redirect(url_for("resident.report_price"))

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
        "report_price.html", products=products, today=date.today(), recent=recent
    )


@bp.route("/alerts", methods=["GET", "POST"])
@resident_only
def alerts():
    products = sorted(Product.all(), key=lambda p: p.product_name)
    product_map = {p.product_id: p for p in products}

    if request.method == "POST":
        product_id = request.form.get("product_id", type=int)
        threshold = request.form.get("threshold", type=float)
        direction = request.form.get("direction", "above")
        if product_id not in product_map or threshold is None or not threshold > 0 or direction not in ("above", "below"):
            flash("Choose a commodity and a valid price threshold.", "danger")
        else:
            PriceAlertRule(
                user_id=current_user.user_id, product_id=product_id,
                direction=direction, threshold=threshold,
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
    """Handle profile picture upload for the resident."""
    photo = request.files.get("profile_pic")
    if not photo or not photo.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("resident.profile"))

    if not allowed_file(photo.filename):
        flash("Only PNG, JPG, and JPEG files are allowed.", "danger")
        return redirect(url_for("resident.profile"))

    ext = photo.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"profile_{current_user.user_id}_{uuid.uuid4().hex[:8]}.{ext}")

    profile_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "profile")
    os.makedirs(profile_dir, exist_ok=True)
    photo.save(os.path.join(profile_dir, filename))

    current_user.profile_pic = f"uploads/profile/{filename}"
    current_user.save()

    flash("Profile picture updated successfully!", "success")
    return redirect(url_for("resident.profile"))


@bp.route("/scan")
@resident_only
def scan_qr():
    """QR code scanner page for residents."""
    return render_template("scan.html")


@bp.route("/prices/history")
@resident_only
def price_history():
    product_id = request.args.get("product_id", type=int)
    products = sorted(Product.all(), key=lambda p: p.product_name)
    return render_template("price_history.html", products=products, selected_product=product_id)


@bp.route("/api/prices/history-data")
@resident_only
def price_history_data():
    """JSON feed used by Chart.js on the price history page."""
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