"""
DTI (Department of Trade and Industry) portal.

DTI's job is different from the LGU's:
- LGU: runs the local markets — verifies resident reports, manages vendors, handles
  price-spike alerts, and escalates serious cases.
- DTI: sets the Suggested Retail Prices (SRP), monitors whether vendors comply with
  them, takes over cases the LGU escalates, and issues notices of price violation
  under the Price Act (RA 7581).
"""
import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from models import Alert, AuditLog, DtiNotice, DtiSrp, Listing, Market, PriceReport, Product, ReportExport, User
from utils import file_store
from utils.helpers import latest_srp_by_product, localtime, log_action, role_required
from utils.notifications import notify

bp = Blueprint("dti", __name__)
dti_only = role_required("dti_da", "admin")

CITIES = ("Cebu City", "Lapu-Lapu City")
MONITOR_DAYS = 14  # verified reports from this many days count as the current market price
OUTCOMES = {
    "notice_issued": "Notice of violation issued",
    "vendor_complied": "Vendor complied (price lowered)",
    "price_justified": "Price increase justified",
    "no_violation": "No violation found",
    "other": "Other",
}


# ---- shared helpers ---------------------------------------------------------
def _vendor_name(v):
    return (v.business_name or v.full_name or f"Vendor #{v.user_id}") if v else "Unknown vendor"


def _refresh_notices():
    """A notice counts as complied once the vendor's price is back at or below the SRP."""
    listings = Listing.index()
    for n in DtiNotice.find(status="issued"):
        l = listings.get(n.listing_id)
        if l is None or (l.price is not None and float(l.price) <= float(n.srp)):
            n.status, n.complied_at = "complied", datetime.utcnow()
            n.save()


def _compliance(city=None):
    """Per-commodity SRP comparison, the vendor listings priced above SRP, and the overall compliance rate."""
    srps = latest_srp_by_product()
    products, markets = Product.index(), Market.index()
    vendors = {v.user_id: v for v in User.find(role="vendor")}
    open_notice = {n.listing_id: n for n in DtiNotice.find(status="issued")}

    since = date.today() - timedelta(days=MONITOR_DAYS)
    prices = defaultdict(list)
    for r in PriceReport.find(status="verified"):
        if r.product_id in srps and r.report_date and r.report_date >= since:
            m = markets.get(r.market_id)
            if city and (m is None or m.city != city):
                continue
            prices[r.product_id].append(float(r.price))

    counts = defaultdict(lambda: [0, 0])  # product_id -> [listings, above SRP]
    violations = []
    for l in Listing.all():
        if not l.product_id or l.product_id not in srps or not l.price:
            continue
        v = vendors.get(l.vendor_id)
        if v is None or v.vendor_status == "suspended":
            continue
        m = markets.get(v.market_id)
        if city and (m is None or m.city != city):
            continue
        srp = float(srps[l.product_id].srp_price)
        counts[l.product_id][0] += 1
        if float(l.price) > srp:
            counts[l.product_id][1] += 1
            p = products.get(l.product_id)
            violations.append({
                "listing": l, "vendor": v, "vendor_name": _vendor_name(v),
                "market": m.market_name if m else "—", "city": m.city if m else "",
                "product": p.product_name if p else l.name, "unit": l.unit or (p.unit if p else "unit"),
                "price": float(l.price), "srp": srp, "over_pct": (float(l.price) - srp) / srp * 100,
                "notice": open_notice.get(l.listing_id),
            })

    rows = []
    for pid, s in srps.items():
        p = products.get(pid)
        if p is None:
            continue
        srp = float(s.srp_price)
        vals = prices.get(pid, [])
        avg = sum(vals) / len(vals) if vals else None
        total, above = counts[pid]
        rows.append({
            "product": p, "srp": srp, "bulletin_date": s.bulletin_date, "source": s.source,
            "avg": avg, "pct": (avg - srp) / srp * 100 if avg is not None else None,
            "reports": len(vals), "listings": total, "above": above,
        })
    rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0), r["product"].product_name.lower()))
    violations.sort(key=lambda v: -v["over_pct"])

    total = sum(c[0] for c in counts.values())
    above = sum(c[1] for c in counts.values())
    rate = round(100 * (total - above) / total) if total else None
    return rows, violations, rate


def _case_parties(alert, vendors=None):
    """The report behind an escalated alert, the vendor it concerns, and that vendor's listing."""
    report = PriceReport.get(alert.report_id) if alert.report_id else None
    if report is None:
        return None, None, None
    vendor = None
    reporter = User.get(report.user_id) if report.user_id else None
    if reporter and reporter.role == "vendor":
        vendor = reporter
    elif report.vendor_name:
        pool = vendors if vendors is not None else User.find(role="vendor")
        vendor = next((v for v in pool if _vendor_name(v).lower() == report.vendor_name.strip().lower()), None)
    listing = None
    if vendor:
        listing = next((l for l in Listing.find(vendor_id=vendor.user_id) if l.product_id == report.product_id), None)
    return report, vendor, listing


def _cases(state):
    """Escalated alerts. state: open | closed."""
    alerts = [a for a in Alert.all() if a.dti_status or a.status == "escalated"]
    for a in alerts:
        a.dti_status = a.dti_status or "new"
    wanted = [a for a in alerts if (a.dti_status == "closed") == (state == "closed")]
    wanted.sort(key=lambda a: a.dti_updated_at or a.escalated_at or a.created_at, reverse=True)
    return wanted


# ---- dashboard --------------------------------------------------------------
@bp.route("/dashboard")
@dti_only
def dashboard():
    _refresh_notices()
    rows, violations, rate = _compliance()
    open_cases = _cases("open")
    srps = latest_srp_by_product()
    last_bulletin = max((s.bulletin_date for s in srps.values() if s.bulletin_date), default=None)
    shared = [e for e in ReportExport.find(shared_with_agency=True)]
    shared.sort(key=lambda e: e.created_at, reverse=True)
    return render_template(
        "dti_dashboard.html",
        rate=rate, srp_count=len(srps), last_bulletin=last_bulletin,
        open_cases=open_cases, new_cases=[c for c in open_cases if c.dti_status == "new"],
        notices_open=DtiNotice.count(status="issued"),
        above_srp=[r for r in rows if r["pct"] is not None and r["pct"] > 0][:5],
        violations=violations[:3], violation_count=len(violations),
        shared=shared[:3],
    )


# ---- SRP bulletins ----------------------------------------------------------
@bp.route("/srp", methods=["GET", "POST"])
@dti_only
def srp():
    if request.method == "POST":
        product = Product.get(request.form.get("product_id", type=int) or 0)
        price = request.form.get("srp_price", type=float)
        try:
            effective = date.fromisoformat(request.form.get("bulletin_date") or str(date.today()))
        except ValueError:
            effective = None
        source = (request.form.get("source") or "").strip()[:120] or "DTI SRP Bulletin"
        if product is None or price is None or not price > 0 or effective is None:
            flash("Choose a commodity and enter a valid SRP and date.", "danger")
            return redirect(url_for("dti.srp"))
        DtiSrp(product_id=product.product_id, srp_price=round(price, 2), bulletin_date=effective, source=source).save()
        log_action(current_user, "srp_updated", f"{product.product_name}: ₱{price:,.2f}/{product.unit} ({source})")
        flash(f"SRP for {product.product_name} set to ₱{price:,.2f} / {product.unit}.", "success")
        return redirect(url_for("dti.srp"))

    products = sorted(Product.all(), key=lambda p: p.product_name.lower())
    history = defaultdict(list)
    for s in DtiSrp.all():
        history[s.product_id].append(s)
    for items in history.values():
        items.sort(key=lambda s: (s.bulletin_date or date.min, s.srp_id), reverse=True)
    with_srp = [p for p in products if history.get(p.product_id)]
    without = [p for p in products if not history.get(p.product_id)]
    return render_template("dti_srp.html", products=products, with_srp=with_srp, without=without,
                           history=history, today=date.today(), focus=request.args.get("product_id", type=int))


@bp.route("/srp/commodity", methods=["POST"])
@dti_only
def add_commodity():
    name = (request.form.get("product_name") or "").strip()[:80]
    unit = (request.form.get("unit") or "kg").strip()[:20] or "kg"
    if not name:
        flash("Enter the commodity name.", "danger")
        return redirect(url_for("dti.srp"))
    existing = next((p for p in Product.all() if p.product_name.lower() == name.lower()), None)
    if existing:
        flash(f"{existing.product_name} is already listed.", "info")
        return redirect(url_for("dti.srp", product_id=existing.product_id))
    p = Product(product_name=name, unit=unit, category=(request.form.get("category") or "others").strip().lower()[:40]).save()
    log_action(current_user, "commodity_added", f"{name} ({unit})")
    flash(f"{name} added. You can now set its SRP.", "success")
    return redirect(url_for("dti.srp", product_id=p.product_id))


# ---- compliance monitoring ----------------------------------------------------
@bp.route("/monitor")
@dti_only
def monitor():
    _refresh_notices()
    city = request.args.get("city")
    city = city if city in CITIES else None
    rows, violations, rate = _compliance(city)
    return render_template("dti_monitor.html", rows=rows, violations=violations, rate=rate, city=city,
                           cities=CITIES, days=MONITOR_DAYS)


@bp.route("/notices", methods=["POST"])
@dti_only
def issue_notice():
    listing = Listing.get_or_404(request.form.get("listing_id", type=int) or 0)
    vendor = User.get(listing.vendor_id)
    srp_entry = latest_srp_by_product().get(listing.product_id)
    back = request.form.get("next") or url_for("dti.monitor")
    if not back.startswith("/"):
        back = url_for("dti.monitor")
    if vendor is None or srp_entry is None:
        flash("This listing has no SRP to compare against.", "danger")
        return redirect(back)
    srp = float(srp_entry.srp_price)
    if float(listing.price) <= srp:
        flash(f"{listing.name} is already at or below the SRP — no notice needed.", "info")
        return redirect(back)
    if DtiNotice.first(listing_id=listing.listing_id, status="issued"):
        flash("A notice for this listing is already pending.", "info")
        return redirect(back)

    product = Product.get(listing.product_id)
    unit = listing.unit or (product.unit if product else "unit")
    note = (request.form.get("message") or "").strip()[:500]
    alert_id = request.form.get("alert_id", type=int)
    message = (f"Your price for {listing.name} (₱{float(listing.price):,.2f}/{unit}) is above the DTI Suggested "
               f"Retail Price of ₱{srp:,.2f}/{unit}. Under the Price Act (RA 7581), please adjust your price to "
               f"the SRP or submit a justification to DTI.") + (f" Note from DTI: {note}" if note else "")
    notice = DtiNotice(
        vendor_id=vendor.user_id, listing_id=listing.listing_id, product_id=listing.product_id,
        product_name=product.product_name if product else listing.name, price=float(listing.price), srp=srp,
        alert_id=alert_id, message=message, issued_by=current_user.user_id,
    ).save()
    notify(vendor.user_id, "Notice of price violation from DTI", message[:300], link="/vendor/dashboard",
           icon="gavel", tone="bad")
    if alert_id:
        alert = Alert.get(alert_id)
        if alert:
            alert.dti_status = "investigating" if alert.dti_status in (None, "new") else alert.dti_status
            alert.dti_handled_by, alert.dti_updated_at = current_user.user_id, datetime.utcnow()
            alert.save()
    log_action(current_user, "dti_notice_issued", f"#{notice.notice_id} {_vendor_name(vendor)} — {listing.name}")
    flash(f"Notice of violation sent to {_vendor_name(vendor)}.", "success")
    return redirect(back)


@bp.route("/notices/<int:notice_id>/close", methods=["POST"])
@dti_only
def close_notice(notice_id):
    n = DtiNotice.get_or_404(notice_id)
    n.status = "closed"
    n.save()
    log_action(current_user, "dti_notice_closed", f"#{notice_id}")
    flash("Notice closed.", "info")
    return redirect(url_for("dti.cases", tab="notices"))


# ---- escalated cases ----------------------------------------------------------
@bp.route("/cases")
@dti_only
def cases():
    _refresh_notices()
    tab = request.args.get("tab", "open")
    tab = tab if tab in ("open", "closed", "notices") else "open"
    vendors = User.find(role="vendor")
    items = []
    if tab in ("open", "closed"):
        notices_by_listing = defaultdict(list)
        for n in DtiNotice.all():
            notices_by_listing[n.listing_id].append(n)
        for a in _cases(tab):
            report, vendor, listing = _case_parties(a, vendors)
            items.append({"alert": a, "report": report, "vendor": vendor, "vendor_name": _vendor_name(vendor) if vendor
                          else (report.vendor_name if report and report.vendor_name else None),
                          "listing": listing,
                          "notices": sorted(notices_by_listing.get(listing.listing_id, []) if listing else [],
                                            key=lambda n: n.created_at, reverse=True)})
    notices = []
    if tab == "notices":
        vmap = {v.user_id: v for v in vendors}
        notices = sorted(DtiNotice.all(), key=lambda n: n.created_at, reverse=True)
        for n in notices:
            n.vendor_label = _vendor_name(vmap.get(n.vendor_id))
    return render_template("dti_cases.html", tab=tab, items=items, notices=notices, outcomes=OUTCOMES,
                           counts={"open": len(_cases("open")), "notices": DtiNotice.count(status="issued")})


@bp.route("/cases/<int:alert_id>/update", methods=["POST"])
@dti_only
def update_case(alert_id):
    alert = Alert.get_or_404(alert_id)
    action = request.form.get("action")
    notes = (request.form.get("notes") or "").strip()[:1000]
    if action == "investigate":
        alert.dti_status = "investigating"
        msg = "Case marked as under investigation."
    elif action == "close":
        outcome = request.form.get("outcome")
        if outcome not in OUTCOMES:
            flash("Choose the outcome of the case.", "danger")
            return redirect(url_for("dti.cases"))
        alert.dti_status, alert.dti_outcome = "closed", outcome
        alert.status, alert.is_resolved = "resolved", True
        alert.resolved_by, alert.resolved_at = current_user.user_id, datetime.utcnow()
        msg = f"Case closed: {OUTCOMES[outcome]}."
    elif action == "reopen":
        alert.dti_status, alert.dti_outcome = "investigating", None
        alert.status, alert.is_resolved = "escalated", False
        msg = "Case reopened."
    else:
        abort(400)
    if notes:
        stamp = f"[{localtime(datetime.utcnow(), '%b %d, %Y')}] {notes}"
        alert.dti_notes = (alert.dti_notes + "\n" + stamp) if alert.dti_notes else stamp
    alert.dti_handled_by, alert.dti_updated_at = current_user.user_id, datetime.utcnow()
    alert.save()
    log_action(current_user, f"dti_case_{action}", f"Alert #{alert_id}: {alert.product_name}")
    flash(msg, "success")
    return redirect(url_for("dti.cases", tab="closed" if action == "close" else "open"))


# ---- reports ------------------------------------------------------------------
@bp.route("/reports")
@dti_only
def reports():
    shared = sorted(ReportExport.find(shared_with_agency=True), key=lambda e: e.created_at, reverse=True)
    lgus = {u.user_id: u for u in User.find(role="lgu_staff")}
    for e in shared:
        u = lgus.get(e.generated_by)
        e.lgu_label = (u.org_name or u.full_name) if u else "LGU"
    return render_template("dti_reports.html", shared=shared[:30], cities=CITIES)


@bp.route("/reports/<int:export_id>/download")
@dti_only
def download_shared(export_id):
    export = ReportExport.get_or_404(export_id)
    if not export.shared_with_agency and current_user.role != "admin":
        abort(404)
    mimetypes = {"pdf": "application/pdf", "csv": "text/csv",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    data = None
    if file_store.is_stored(export.file_path):
        data, _meta = file_store.load(export.file_path)
    if data is None:
        abort(404)
    return send_file(io.BytesIO(data), mimetype=mimetypes.get(export.file_type, "application/octet-stream"),
                     as_attachment=True, download_name=f"{export.title or 'presyowatch-report'}.{export.file_type}")


def _csv(name, header, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    log_action(current_user, "dti_export", name)
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.route("/reports/compliance.csv")
@dti_only
def export_compliance():
    city = request.args.get("city")
    city = city if city in CITIES else None
    rows, violations, rate = _compliance(city)
    stamp = f"{date.today():%Y-%m-%d}"
    if request.args.get("kind") == "violations":
        return _csv(f"dti-vendors-above-srp-{stamp}.csv",
                    ["Vendor", "Market", "City", "Commodity", "Unit", "Vendor price", "SRP", "% above SRP", "Notice"],
                    [[v["vendor_name"], v["market"], v["city"], v["product"], v["unit"], f"{v['price']:.2f}",
                      f"{v['srp']:.2f}", f"{v['over_pct']:.1f}", "Pending" if v["notice"] else "None"] for v in violations])
    return _csv(f"dti-srp-compliance-{stamp}.csv",
                ["Commodity", "Unit", "SRP", "SRP effective", f"Avg verified price ({MONITOR_DAYS} days)",
                 "% vs SRP", "Verified reports", "Vendor listings", "Listings above SRP"],
                [[r["product"].product_name, r["product"].unit, f"{r['srp']:.2f}", r["bulletin_date"] or "",
                  f"{r['avg']:.2f}" if r["avg"] is not None else "", f"{r['pct']:.1f}" if r["pct"] is not None else "",
                  r["reports"], r["listings"], r["above"]] for r in rows])


@bp.route("/reports/notices.csv")
@dti_only
def export_notices():
    vendors = {v.user_id: v for v in User.find(role="vendor")}
    notices = sorted(DtiNotice.all(), key=lambda n: n.created_at, reverse=True)
    return _csv(f"dti-notices-{date.today():%Y-%m-%d}.csv",
                ["Notice #", "Date issued", "Vendor", "Commodity", "Price at notice", "SRP", "Status", "Complied on"],
                [[n.notice_id, f"{n.created_at:%Y-%m-%d}", _vendor_name(vendors.get(n.vendor_id)), n.product_name,
                  f"{float(n.price):.2f}", f"{float(n.srp):.2f}", n.status.title(),
                  f"{n.complied_at:%Y-%m-%d}" if n.complied_at else ""] for n in notices])


# ---- profile --------------------------------------------------------------------
@bp.route("/profile")
@dti_only
def profile():
    logs = sorted(AuditLog.find(user_id=current_user.user_id), key=lambda l: l.created_at, reverse=True)[:8]
    return render_template("dti_profile.html", logs=logs,
                           notices_issued=DtiNotice.count(issued_by=current_user.user_id),
                           cases_closed=sum(1 for a in Alert.find(dti_status="closed")
                                            if a.dti_handled_by == current_user.user_id))
