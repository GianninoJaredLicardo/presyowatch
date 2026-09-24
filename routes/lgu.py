import io
import os
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, send_file, abort, current_app
)
from flask_login import current_user

from config import PLANS
from models import (
    Alert, AuditLog, BillingRecord, Market, PriceReport, Product, ReportExport,
    Subscription, User,
)
from utils.helpers import get_settings, log_action, role_required, save_settings
from utils.qr_generator import generate_qr_bytes, market_price_search_url
from utils.report_generator import build_csv_report, build_pdf_report, build_xlsx_report

bp = Blueprint("lgu", __name__)
lgu_only = role_required("lgu_staff", "dti_da", "admin")

OPEN_ALERT = ("open", "acknowledged", "escalated")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _users_for(reports):
    return {uid: User.get(uid) for uid in {r.user_id for r in reports}}


def _open_alerts():
    return sorted(
        (a for a in Alert.all() if a.status in OPEN_ALERT),
        key=lambda a: a.created_at, reverse=True,
    )


# ---- home ---------------------------------------------------------------
@bp.route("/dashboard")
@lgu_only
def dashboard():
    open_alerts = _open_alerts()
    vendors = User.find(role="vendor")
    sub = _subscription()

    since = datetime.utcnow() - timedelta(hours=24)
    recent_reports = [r for r in PriceReport.all() if r.created_at >= since]
    events = []
    if recent_reports:
        newest = max(r.created_at for r in recent_reports)
        events.append((f"{len(recent_reports)} new price report{'s' if len(recent_reports) != 1 else ''} submitted", newest))
    for v in sorted((v for v in vendors if v.vendor_verified_at), key=lambda v: v.vendor_verified_at, reverse=True)[:2]:
        events.append((f"Vendor badge approved: {v.business_name or v.full_name}", v.vendor_verified_at))
    pending_vendors = [v for v in vendors if v.vendor_status == "pending"]
    if pending_vendors:
        events.append((
            f"New vendor registration pending: {len(pending_vendors)} vendor{'s' if len(pending_vendors) != 1 else ''}",
            max(v.created_at for v in pending_vendors),
        ))
    events.sort(key=lambda e: e[1], reverse=True)

    return render_template(
        "lgu_dashboard.html",
        plan=PLANS[sub.plan], sub=sub,
        pending_count=PriceReport.count(status="pending") + PriceReport.count(status="flagged"),
        alert_count=len(open_alerts),
        vendor_count=len(vendors),
        verified_count=PriceReport.count(status="verified"),
        spike=open_alerts[0] if open_alerts else None,
        events=events[:5],
    )


# ---- verification -----------------------------------------------------------
@bp.route("/queue")
@lgu_only
def verification_queue():
    status = request.args.get("status", "pending")
    if status not in ("all", "pending", "verified", "rejected", "flagged"):
        status = "pending"
    reports = PriceReport.all() if status == "all" else PriceReport.find(status=status)
    reports.sort(key=lambda r: r.created_at, reverse=True)
    reports = reports[:100]

    products, markets, users = Product.index(), Market.index(), _users_for(reports)
    rows = [
        (r, products[r.product_id], markets[r.market_id], users[r.user_id])
        for r in reports
        if r.product_id in products and r.market_id in markets and users.get(r.user_id)
    ]
    return render_template("verification_queue.html", rows=rows, status=status)


@bp.route("/reports/<int:report_id>")
@lgu_only
def report_detail(report_id):
    report = PriceReport.get_or_404(report_id)
    return render_template(
        "report_detail.html", report=report, product=Product.get_or_404(report.product_id),
        market=Market.get_or_404(report.market_id), reporter=User.get_or_404(report.user_id),
        alerts=Alert.find(report_id=report_id),
    )


def _review_report(report_id, status, label):
    report = PriceReport.get_or_404(report_id)
    report.status = status
    report.verified_by = current_user.user_id
    report.verified_at = datetime.utcnow()
    report.verification_notes = request.form.get("notes", "").strip()[:500] or None
    report.save()
    log_action(current_user, f"report_{status}", f"Report #{report_id}")
    flash(f"Report #{report_id} {label}.", "success" if status == "verified" else "info")
    dest = request.form.get("next", "")
    return redirect(dest if dest.startswith("/lgu/") else url_for("lgu.verification_queue"))


@bp.route("/reports/<int:report_id>/verify", methods=["POST"])
@lgu_only
def verify_report(report_id):
    return _review_report(report_id, "verified", "approved")


@bp.route("/reports/<int:report_id>/reject", methods=["POST"])
@lgu_only
def reject_report(report_id):
    return _review_report(report_id, "rejected", "rejected")


@bp.route("/reports/<int:report_id>/flag", methods=["POST"])
@lgu_only
def flag_report(report_id):
    return _review_report(report_id, "flagged", "flagged for follow-up")


# ---- stats --------------------------------------------------------------------
@bp.route("/stats")
@lgu_only
def stats():
    days = request.args.get("days", 7, type=int)
    if days not in (7, 30, 90):
        days = 7
    today = date.today()
    since = today - timedelta(days=days - 1)
    prev_since = since - timedelta(days=days)

    reports = PriceReport.find(("report_date", ">=", since))
    prev_reports = PriceReport.find(("report_date", ">=", prev_since), ("report_date", "<", since))
    products, markets = Product.index(), Market.index()
    vendors = User.find(role="vendor")
    threshold = get_settings()["spike_threshold"]

    by_weekday, spikes_by_weekday = Counter(), Counter()
    for r in reports:
        by_weekday[r.report_date.weekday()] += 1
        if r.is_spike:
            spikes_by_weekday[r.report_date.weekday()] += 1

    def bars(counter):
        top = max(counter.values(), default=0) or 1
        return [{"label": WEEKDAYS[i], "value": counter.get(i, 0), "pct": round(100 * counter.get(i, 0) / top)}
                for i in range(7)]

    prices, prev_prices, categories = defaultdict(list), defaultdict(list), Counter()
    for r in reports:
        if r.product_id in products:
            prices[r.product_id].append(float(r.price))
            categories[products[r.product_id].category or "General"] += 1
    for r in prev_reports:
        prev_prices[r.product_id].append(float(r.price))

    summary = []
    for pid, vals in prices.items():
        avg = sum(vals) / len(vals)
        change = None
        if prev_prices.get(pid):
            prev_avg = sum(prev_prices[pid]) / len(prev_prices[pid])
            change = round(100 * (avg - prev_avg) / prev_avg, 1) if prev_avg else None
        summary.append({"name": products[pid].product_name, "avg": avg, "lo": min(vals), "hi": max(vals),
                        "change": change, "n": len(vals)})
    summary.sort(key=lambda s: -s["n"])

    total_cat = sum(categories.values()) or 1
    by_category = [{"name": k, "pct": round(100 * v / total_cat)} for k, v in categories.most_common(5)]

    market_counts = Counter(r.market_id for r in reports)
    vendors_per_market = Counter(v.market_id for v in vendors)
    top_total = max(market_counts.values(), default=0) or 1
    top_markets = [
        {"name": markets[mid].market_name, "reports": n, "vendors": vendors_per_market.get(mid, 0),
         "pct": round(100 * n / top_total)}
        for mid, n in market_counts.most_common(3) if mid in markets
    ]

    verified_v = sum(1 for v in vendors if v.is_verified_vendor)
    pending_v = sum(1 for v in vendors if not v.is_verified_vendor and v.vendor_status == "pending")
    badge = {"verified": verified_v, "pending": pending_v, "unverified": len(vendors) - verified_v - pending_v}
    badge_total = len(vendors) or 1

    major = sum(1 for r in reports if r.is_spike or (r.spike_percent or 0) >= threshold)
    minor = sum(1 for r in reports if not (r.is_spike or (r.spike_percent or 0) >= threshold)
                and (r.spike_percent or 0) >= 5)
    fair = len(reports) - major - minor
    n = len(reports) or 1
    compliance = {
        "fair": round(100 * fair / n), "minor": round(100 * minor / n), "major": round(100 * major / n),
        "has_data": bool(reports),
    }

    return render_template(
        "lgu_stats.html", days=days,
        totals={"reports": len(reports), "spikes": sum(1 for r in reports if r.is_spike),
                "vendors": sum(1 for v in vendors if v.is_active_flag),
                "verified": sum(1 for r in reports if r.status == "verified")},
        report_bars=bars(by_weekday), spike_bars=bars(spikes_by_weekday),
        summary=summary[:8], by_category=by_category, top_markets=top_markets,
        badge=badge, badge_pct={k: round(100 * v / badge_total) for k, v in badge.items()},
        compliance=compliance, threshold=threshold,
    )


# ---- subscription ---------------------------------------------------------------
def _subscription():
    sub = Subscription.first(user_id=current_user.user_id)
    if sub is None:
        now = datetime.utcnow()
        sub = Subscription(
            user_id=current_user.user_id, plan="quarterly", started_at=now,
            renews_at=now + timedelta(days=PLANS["quarterly"]["days"]),
        ).save()
        BillingRecord(user_id=current_user.user_id,
                      description=f"Quarterly Plan — {now:%b %Y}",
                      amount=PLANS["quarterly"]["price"]).save()
    return sub


def _charge(plan_key, note=""):
    plan = PLANS[plan_key]
    BillingRecord(user_id=current_user.user_id,
                  description=f"{plan['name']} Plan — {datetime.utcnow():%b %Y}{note}",
                  amount=plan["price"]).save()


@bp.route("/subscription")
@lgu_only
def subscription():
    sub = _subscription()
    plan = PLANS[sub.plan]
    now = datetime.utcnow()
    total = plan["days"] * 86400
    left = max(0, (sub.renews_at - now).total_seconds())
    elapsed_pct = max(0, min(100, round(100 * (1 - left / total)))) if total else 100

    limit = plan["markets"]
    usage = [
        {"label": "Reports Submitted", "value": PriceReport.count(), "limit": None, "soft": 5000},
        {"label": "Markets Monitored", "value": Market.count(), "limit": limit, "soft": limit or 1},
        {"label": "QR Codes Generated", "value": sub.qr_generated, "limit": None, "soft": 100},
        {"label": "Report Downloads", "value": ReportExport.count(generated_by=current_user.user_id),
         "limit": None, "soft": 100},
    ]
    for u in usage:
        u["pct"] = min(100, round(100 * u["value"] / u["soft"])) if u["soft"] else 0

    history = sorted(BillingRecord.find(user_id=current_user.user_id), key=lambda b: b.created_at, reverse=True)[:6]
    return render_template(
        "lgu_subscription.html", sub=sub, plan=plan, plans=PLANS, days_left=max(0, (sub.renews_at - now).days),
        elapsed_pct=elapsed_pct, usage=usage, history=history,
    )


@bp.route("/subscription/renew", methods=["POST"])
@lgu_only
def renew_subscription():
    sub = _subscription()
    plan = PLANS[sub.plan]
    base = max(datetime.utcnow(), sub.renews_at)
    sub.renews_at = base + timedelta(days=plan["days"])
    sub.status = "active"
    sub.save()
    current_user.subscription_status = "active"
    current_user.save()
    _charge(sub.plan)
    log_action(current_user, "subscription_renewed", plan["name"])
    flash(f"{plan['name']} plan renewed (simulated payment).", "success")
    return redirect(url_for("lgu.subscription"))


@bp.route("/subscription/plan", methods=["POST"])
@lgu_only
def change_plan():
    key = request.form.get("plan")
    if key not in PLANS:
        abort(400)
    sub = _subscription()
    now = datetime.utcnow()
    sub.plan, sub.status, sub.started_at = key, "active", now
    sub.renews_at = now + timedelta(days=PLANS[key]["days"])
    sub.save()
    current_user.subscription_status = "active"
    current_user.save()
    _charge(key)
    log_action(current_user, "plan_changed", PLANS[key]["name"])
    flash(f"Switched to the {PLANS[key]['name']} plan (simulated payment).", "success")
    return redirect(url_for("lgu.subscription"))


@bp.route("/subscription/payment", methods=["POST"])
@lgu_only
def update_payment():
    kind = request.form.get("payment_type")
    labels = {"card": "Card", "gcash": "GCash", "maya": "Maya", "bank": "Bank Transfer"}
    if kind not in labels:
        abort(400)
    last4 = request.form.get("last4", "").strip()
    expiry = request.form.get("expiry", "").strip()
    if kind == "card" and not (len(last4) == 4 and last4.isdigit()):
        flash("Enter the last 4 digits of the card.", "danger")
        return redirect(url_for("lgu.subscription"))
    sub = _subscription()
    sub.payment_type, sub.payment_label = kind, labels[kind]
    sub.payment_last4 = last4 if kind == "card" else None
    sub.payment_expiry = expiry[:5] if kind == "card" else None
    sub.save()
    log_action(current_user, "payment_method_changed", labels[kind])
    flash("Payment method updated. Only the card's last 4 digits are stored.", "success")
    return redirect(url_for("lgu.subscription"))


@bp.route("/subscription/cancel", methods=["POST"])
@lgu_only
def cancel_subscription():
    sub = _subscription()
    sub.status = "cancelled"
    sub.save()
    current_user.subscription_status = "inactive"
    current_user.save()
    log_action(current_user, "subscription_cancelled")
    flash("Subscription cancelled. Renew any time to reactivate.", "info")
    return redirect(url_for("lgu.subscription"))


# ---- manage: vendors + reports ---------------------------------------------------
@bp.route("/manage")
@lgu_only
def manage():
    tab = request.args.get("tab", "vendors")
    if tab == "reports":
        return _manage_reports()
    return _manage_vendors()


def _manage_vendors():
    status = request.args.get("status", "all")
    q = request.args.get("q", "").strip().lower()
    markets = Market.index()
    vendors = User.find(role="vendor")

    def keep(v):
        if q and q not in (v.business_name or "").lower() and q not in v.full_name.lower():
            return False
        return {
            "all": True, "verified": v.is_verified_vendor,
            "active": v.vendor_status == "active", "pending": v.vendor_status == "pending",
            "suspended": v.vendor_status == "suspended",
        }.get(status, True)

    vendors = sorted((v for v in vendors if keep(v)), key=lambda v: (v.business_name or v.full_name).lower())
    return render_template("lgu_manage_vendors.html", vendors=vendors, markets=markets, status=status,
                           q=request.args.get("q", ""))


def _gather_export_rows(date_from, date_to, market_id):
    reports = PriceReport.find(("report_date", ">=", date_from), ("report_date", "<=", date_to))
    reports.sort(key=lambda r: r.report_date)
    products, markets, users = Product.index(), Market.index(), _users_for(reports)
    rows = []
    for r in reports:
        product, market, user = products.get(r.product_id), markets.get(r.market_id), users.get(r.user_id)
        if product is None or market is None or user is None:
            continue
        if market_id and r.market_id != market_id:
            continue
        rows.append({
            "product": product.product_name, "unit": product.unit, "city": market.city,
            "market": market.market_name, "price": float(r.price), "status": r.status,
            "is_spike": r.is_spike, "report_date": r.report_date, "reporter": user.full_name,
        })
    return rows


def _manage_reports():
    today = date.today()
    exports = ReportExport.find(generated_by=current_user.user_id)
    if current_user.role == "dti_da":
        exports += [e for e in ReportExport.find(shared_with_agency=True)
                    if e.generated_by != current_user.user_id]
    exports.sort(key=lambda e: e.created_at, reverse=True)
    return render_template(
        "lgu_manage_reports.html", alerts=_open_alerts()[:20], markets=sorted(Market.all(), key=lambda m: m.market_name),
        exports=exports[:10], today=today,
    )


def _vendor_action(vendor_id, message, **changes):
    vendor = User.get_or_404(vendor_id)
    if vendor.role != "vendor":
        abort(404)
    for k, v in changes.items():
        setattr(vendor, k, v)
    vendor.save()
    log_action(current_user, "vendor_" + message.split()[0].lower(), vendor.business_name or vendor.full_name)
    flash(f"{vendor.business_name or vendor.full_name}: {message}.", "success")
    return redirect(request.referrer or url_for("lgu.manage"))


@bp.route("/vendors/<int:vendor_id>/suspend", methods=["POST"])
@lgu_only
def suspend_vendor(vendor_id):
    return _vendor_action(vendor_id, "suspended", vendor_status="suspended", is_active_flag=False)


@bp.route("/vendors/<int:vendor_id>/reactivate", methods=["POST"])
@lgu_only
def reactivate_vendor(vendor_id):
    return _vendor_action(vendor_id, "reactivated", vendor_status="active", is_active_flag=True)


@bp.route("/vendors/<int:vendor_id>/approve", methods=["POST"])
@lgu_only
def approve_vendor(vendor_id):
    return _vendor_action(vendor_id, "badge approved", vendor_status="active", is_active_flag=True,
                          is_verified_vendor=True, vendor_verified_at=datetime.utcnow())


def _alert_action(alert_id, status, label):
    alert = Alert.get_or_404(alert_id)
    alert.status = status
    if status == "resolved":
        alert.is_resolved = True
        alert.resolved_by = current_user.user_id
        alert.resolved_at = datetime.utcnow()
    alert.save()
    log_action(current_user, f"alert_{status}", f"Alert #{alert_id}: {alert.product_name}")
    flash(f"Alert {label}.", "success")
    return redirect(request.referrer or url_for("lgu.dashboard"))


@bp.route("/alerts/<int:alert_id>/acknowledge", methods=["POST"])
@lgu_only
def acknowledge_alert(alert_id):
    return _alert_action(alert_id, "acknowledged", "acknowledged")


@bp.route("/alerts/<int:alert_id>/resolve", methods=["POST"])
@lgu_only
def resolve_alert(alert_id):
    return _alert_action(alert_id, "resolved", "marked as resolved")


@bp.route("/alerts/<int:alert_id>/escalate", methods=["POST"])
@lgu_only
def escalate_alert(alert_id):
    return _alert_action(alert_id, "escalated", "escalated to DTI/DA")


# ---- report generation ------------------------------------------------------------
@bp.route("/reports/generate", methods=["POST"])
@lgu_only
def generate_report():
    today = date.today()
    period = request.form.get("period", "weekly")
    try:
        if period == "custom":
            date_from = date.fromisoformat(request.form.get("date_from", ""))
            date_to = date.fromisoformat(request.form.get("date_to", ""))
        else:
            date_to = today
            date_from = today - timedelta(days={"daily": 0, "weekly": 6, "monthly": 29}.get(period, 6))
    except ValueError:
        flash("Please choose a valid date range.", "danger")
        return redirect(url_for("lgu.manage", tab="reports"))
    if date_from > date_to:
        flash("The start date must be before the end date.", "danger")
        return redirect(url_for("lgu.manage", tab="reports"))

    market_id = request.form.get("market_id", type=int)
    market = Market.get(market_id) if market_id else None
    file_type = request.form.get("file_type", "pdf")
    rows = _gather_export_rows(date_from, date_to, market.market_id if market else None)

    if file_type == "csv":
        data = build_csv_report(rows, date_from, date_to)
    elif file_type == "xlsx":
        data = build_xlsx_report(rows, date_from, date_to)
    else:
        file_type = "pdf"
        data = build_pdf_report(rows, date_from, date_to, city_filter=market.market_name if market else "All Markets")

    os.makedirs(current_app.config["EXPORT_FOLDER"], exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{file_type}"
    with open(os.path.join(current_app.config["EXPORT_FOLDER"], stored), "wb") as fh:
        fh.write(data)

    title = f"{period.capitalize()} Report {date_from:%b %d}-{date_to:%d}" if date_from.month == date_to.month \
        else f"{period.capitalize()} Report {date_from:%b %d}-{date_to:%b %d}"
    share = bool(request.form.get("auto_send"))
    ReportExport(
        generated_by=current_user.user_id, file_type=file_type, file_path=stored, title=title,
        date_from=date_from, date_to=date_to, market_id=market.market_id if market else None,
        size_bytes=len(data), shared_with_agency=share,
    ).save()
    log_action(current_user, "report_generated", f"{title} ({file_type})" + (" — shared with DTI/DA" if share else ""))
    flash(f"'{title}' generated" + (" and shared with DTI/DA." if share else "."), "success")
    return redirect(url_for("lgu.manage", tab="reports"))


@bp.route("/reports/<int:export_id>/download")
@lgu_only
def download_report(export_id):
    export = ReportExport.get_or_404(export_id)
    allowed = export.generated_by == current_user.user_id or current_user.role == "admin" or (
        current_user.role == "dti_da" and export.shared_with_agency)
    path = os.path.join(current_app.config["EXPORT_FOLDER"], os.path.basename(export.file_path))
    if not allowed or not os.path.exists(path):
        abort(404)
    mimetypes = {"pdf": "application/pdf", "csv": "text/csv",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    return send_file(path, mimetype=mimetypes.get(export.file_type), as_attachment=True,
                     download_name=f"{export.title or 'presyowatch-report'}.{export.file_type}")


# ---- QR codes -----------------------------------------------------------------------
@bp.route("/qr-codes")
@lgu_only
def qr_codes():
    markets = sorted(Market.all(), key=lambda m: m.market_name)
    return render_template("lgu_qr_codes.html", markets=markets)


@bp.route("/qr/<int:market_id>/download")
@lgu_only
def download_qr(market_id):
    market = Market.get_or_404(market_id)
    png = generate_qr_bytes(market_price_search_url(request.url_root, market_id))
    _bump_qr(1)
    return send_file(io.BytesIO(png), mimetype="image/png", as_attachment=True,
                     download_name=f"qr-{market.market_name.replace(' ', '-').lower()}.png")


@bp.route("/qr/download-all")
@lgu_only
def download_all_qr():
    buf = io.BytesIO()
    markets = Market.all()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in markets:
            zf.writestr(f"qr-{m.market_name.replace(' ', '-').lower()}.png",
                        generate_qr_bytes(market_price_search_url(request.url_root, m.market_id)))
    buf.seek(0)
    _bump_qr(len(markets))
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="presyowatch-market-qr-codes.zip")


def _bump_qr(n):
    sub = _subscription()
    sub.qr_generated += n
    sub.save()


# ---- profile, settings, logs -------------------------------------------------------------
@bp.route("/profile")
@lgu_only
def profile():
    sub = _subscription()
    return render_template("lgu_profile.html", plan=PLANS[sub.plan], sub=sub, settings=get_settings(),
                           days_left=max(0, (sub.renews_at - datetime.utcnow()).days))


@bp.route("/settings", methods=["POST"])
@lgu_only
def save_system_settings():
    threshold = request.form.get("spike_threshold", type=float)
    if threshold is None or not 1 <= threshold <= 200:
        flash("Spike threshold must be between 1 and 200.", "danger")
        return redirect(url_for("lgu.profile"))
    save_settings(
        spike_threshold=threshold,
        email_notifications=bool(request.form.get("email_notifications")),
        sms_alerts=bool(request.form.get("sms_alerts")),
    )
    log_action(current_user, "settings_changed", f"Spike threshold {threshold:g}%")
    flash("System settings saved.", "success")
    return redirect(url_for("lgu.profile"))


@bp.route("/logs")
@lgu_only
def logs():
    entries = sorted(AuditLog.all(), key=lambda l: l.created_at, reverse=True)[:100]
    return render_template("lgu_logs.html", entries=entries)
