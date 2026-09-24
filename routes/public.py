from datetime import datetime
from io import BytesIO

from flask import (
    Blueprint, render_template, request, send_file, redirect, url_for, flash, abort
)
from flask_login import current_user

from models import Listing, Market, PriceReport, Product, Review, ScanEvent, User
from utils.helpers import vendor_stats
from utils.qr_generator import generate_qr_bytes, market_price_search_url

bp = Blueprint("public", __name__)


@bp.route("/")
def home():
    return render_template("home.html")


@bp.route("/prices/search")
def price_search():
    q = request.args.get("q", "").strip().lower()
    category = request.args.get("category") or None
    market_id = request.args.get("market_id", type=int)
    product_id = request.args.get("product_id", type=int)
    city = request.args.get("city") or None

    products = Product.index()
    markets = Market.index()
    vendors = {u.user_id: u for u in User.find(role="vendor")}
    categories = sorted({p.category for p in products.values() if p.category})

    results = []

    for l in Listing.all():
        vendor = vendors.get(l.vendor_id)
        if vendor is None or vendor.vendor_status == "suspended" or l.stock_status == "out_of_stock":
            continue
        market = markets.get(vendor.market_id)
        results.append({
            "name": l.name, "category": l.category, "seller": vendor.business_name or vendor.full_name,
            "seller_id": vendor.user_id, "verified": vendor.is_verified_vendor,
            "market_id": vendor.market_id, "market": market.market_name if market else "",
            "city": market.city if market else vendor.city, "price": float(l.price), "unit": l.unit,
            "date": l.updated_at, "product_id": l.product_id,
        })

    for r in PriceReport.find(status="verified"):
        product, market = products.get(r.product_id), markets.get(r.market_id)
        if product is None or market is None:
            continue
        results.append({
            "name": product.product_name, "category": product.category,
            "seller": r.vendor_name or "Community report", "seller_id": None, "verified": True,
            "market_id": market.market_id, "market": market.market_name, "city": market.city,
            "price": float(r.price), "unit": product.unit, "date": r.report_date,
            "product_id": r.product_id,
        })

    def keep(row):
        return (
            (not q or q in row["name"].lower() or q in row["seller"].lower())
            and (not category or row["category"] == category)
            and (not market_id or row["market_id"] == market_id)
            and (not product_id or row["product_id"] == product_id)
            and (not city or row["city"] == city)
        )

    results = sorted((r for r in results if keep(r)), key=lambda r: r["price"])[:100]

    return render_template(
        "price_search.html",
        results=results,
        categories=categories,
        markets=sorted(markets.values(), key=lambda m: m.market_name),
        q=request.args.get("q", ""),
        selected_category=category,
        selected_market=market_id,
    )


@bp.route("/vendor/<int:vendor_id>")
def vendor_page(vendor_id):
    vendor = User.get(vendor_id)
    if vendor is None or vendor.role != "vendor" or vendor.vendor_status == "suspended":
        abort(404)

    if request.args.get("src") == "qr":
        ScanEvent(vendor_id=vendor_id).save()

    market = Market.get(vendor.market_id) if vendor.market_id else None
    listings = sorted(Listing.find(vendor_id=vendor_id), key=lambda l: l.name)
    reviews = sorted(Review.find(vendor_id=vendor_id), key=lambda r: r.created_at, reverse=True)[:10]
    updated = max((l.updated_at for l in listings), default=None)

    return render_template(
        "vendor_public.html", vendor=vendor, market=market, listings=listings[:6],
        more=max(0, len(listings) - 6), reviews=reviews, updated=updated, stats=vendor_stats(vendor_id),
    )


@bp.route("/vendor/<int:vendor_id>/review", methods=["POST"])
def add_review(vendor_id):
    if not current_user.is_authenticated:
        flash("Please log in to leave a review.", "warning")
        return redirect(url_for("auth.login"))
    vendor = User.get(vendor_id)
    if vendor is None or vendor.role != "vendor":
        abort(404)
    if current_user.user_id == vendor_id:
        flash("You cannot review your own stall.", "warning")
        return redirect(url_for("public.vendor_page", vendor_id=vendor_id))

    rating = request.form.get("rating", type=int)
    comment = request.form.get("comment", "").strip()
    if rating not in (1, 2, 3, 4, 5) or not comment:
        flash("Please choose a rating and write a short comment.", "danger")
    else:
        Review(
            vendor_id=vendor_id, user_id=current_user.user_id,
            user_name=current_user.full_name, rating=rating, comment=comment[:500],
        ).save()
        flash("Thanks for your feedback!", "success")
    return redirect(url_for("public.vendor_page", vendor_id=vendor_id))


@bp.route("/scan")
def scan():
    return render_template("scan.html")


def _png(data):
    return send_file(BytesIO(generate_qr_bytes(data)), mimetype="image/png")


@bp.route("/qr/market/<int:market_id>.png")
def market_qr(market_id):
    return _png(market_price_search_url(request.url_root, market_id))


@bp.route("/qr/vendor/<int:vendor_id>.png")
def vendor_qr(vendor_id):
    return _png(f"{request.url_root.rstrip('/')}/vendor/{vendor_id}?src=qr")


@bp.route("/qr/site.png")
def site_qr():
    return _png(request.url_root)
