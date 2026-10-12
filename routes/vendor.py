from datetime import date, datetime, timedelta

from flask import Blueprint, abort, render_template, request, redirect, url_for, flash
from flask_login import current_user

from models import Listing, Market, Product, Review, ScanEvent
from utils.helpers import log_action, role_required, vendor_stats
from utils.spike_detection import create_report

bp = Blueprint("vendor", __name__)
vendor_only = role_required("vendor")

STOCK = ("in_stock", "low_stock", "out_of_stock")


def _my_listings():
    return sorted(Listing.find(vendor_id=current_user.user_id), key=lambda l: l.name.lower())


def _own_listing(listing_id):
    listing = Listing.get_or_404(listing_id)
    if listing.vendor_id != current_user.user_id:
        abort(403)
    return listing


def _apply_price(listing, new_price):
    """Update a listing price; for tracked commodities also file a price report so spike detection runs."""
    if new_price == listing.price:
        return None
    listing.prev_price = listing.price
    listing.price = new_price
    listing.updated_at = datetime.utcnow()
    listing.save()
    if listing.product_id and current_user.market_id:
        report, is_spike = create_report(
            current_user.user_id, listing.product_id, current_user.market_id, new_price,
            date.today(), vendor_name=current_user.business_name,
        )
        return is_spike
    return False


def _weekly_scans():
    since = datetime.utcnow() - timedelta(days=7)
    return sum(1 for s in ScanEvent.find(vendor_id=current_user.user_id) if s.created_at >= since)


@bp.route("/dashboard")
@vendor_only
def dashboard():
    listings = _my_listings()
    reviews = sorted(Review.find(vendor_id=current_user.user_id), key=lambda r: r.created_at, reverse=True)
    return render_template(
        "vendor_dashboard.html",
        listings=listings,
        needs_attention=[l for l in listings if l.stock_status != "in_stock"],
        stats=vendor_stats(current_user.user_id),
        scans=_weekly_scans(),
        reviews=reviews[:2],
        market=Market.get(current_user.market_id) if current_user.market_id else None,
    )


@bp.route("/listings")
@vendor_only
def listings():
    q = request.args.get("q", "").strip().lower()
    items = [l for l in _my_listings() if not q or q in l.name.lower()]
    return render_template("vendor_listings.html", listings=items, q=request.args.get("q", ""))


@bp.route("/listings/new", methods=["GET", "POST"])
@bp.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@vendor_only
def listing_form(listing_id=None):
    listing = _own_listing(listing_id) if listing_id else None
    products = sorted(Product.all(), key=lambda p: p.product_name)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", type=float)
        # Category is now a free-text input — accept anything the vendor types
        category = request.form.get("category", "General").strip() or "General"
        unit = request.form.get("unit", "kg").strip() or "kg"
        stock = request.form.get("stock_status", "in_stock")
        product_id = request.form.get("product_id", type=int) or None

        if not name or price is None or not price > 0 or stock not in STOCK:
            flash("Name and a valid price are required.", "danger")
            return redirect(request.url)
        if product_id and Product.get(product_id) is None:
            product_id = None

        if listing is None:
            listing = Listing(vendor_id=current_user.user_id, price=price)
            listing.save()
            new_price = None
        else:
            new_price = price

        listing.name = name
        listing.category = category
        listing.unit = unit
        listing.stock_status = stock
        listing.product_id = product_id
        listing.updated_at = datetime.utcnow()
        listing.save()

        if new_price is not None:
            _apply_price(listing, new_price)
        elif product_id and current_user.market_id:
            create_report(
                current_user.user_id, product_id, current_user.market_id, price,
                date.today(), vendor_name=current_user.business_name,
            )

        log_action(current_user, "listing_saved", name)
        flash("Listing saved.", "success")
        return redirect(url_for("vendor.listings"))

    return render_template("listing_form.html", listing=listing, products=products)


@bp.route("/listings/<int:listing_id>/delete", methods=["POST"])
@vendor_only
def delete_listing(listing_id):
    listing = _own_listing(listing_id)
    listing.delete()
    flash(f"'{listing.name}' removed.", "info")
    return redirect(url_for("vendor.listings"))


@bp.route("/prices")
@vendor_only
def prices():
    return render_template("vendor_prices.html", listings=_my_listings())


@bp.route("/prices/<int:listing_id>/update", methods=["POST"])
@vendor_only
def update_price(listing_id):
    listing = _own_listing(listing_id)
    price = request.form.get("price", type=float)
    if price is None or not price > 0:
        flash("Enter a valid price.", "danger")
    else:
        spike = _apply_price(listing, price)
        flash(f"{listing.name} updated to ₱{price:,.2f}.", "success")
        if spike:
            flash("That price is far above the market baseline and was flagged for LGU review.", "warning")
    return redirect(request.referrer or url_for("vendor.prices"))


@bp.route("/prices/bulk", methods=["GET", "POST"])
@vendor_only
def bulk_prices():
    items = _my_listings()
    if request.method == "POST":
        changed = flagged = 0
        for l in items:
            price = request.form.get(f"price_{l.listing_id}", type=float)
            if price is not None and price > 0 and price != l.price:
                changed += 1
                flagged += bool(_apply_price(l, price))
        flash(
            f"{changed} price(s) updated." + (f" {flagged} flagged for LGU review." if flagged else ""),
            "warning" if flagged else "success",
        )
        return redirect(url_for("vendor.prices"))
    return render_template("vendor_bulk.html", listings=items)


@bp.route("/feedback")
@vendor_only
def feedback():
    reviews = sorted(Review.find(vendor_id=current_user.user_id), key=lambda r: r.created_at, reverse=True)
    return render_template(
        "vendor_feedback.html",
        reviews=reviews,
        stats=vendor_stats(current_user.user_id),
        market=Market.get(current_user.market_id) if current_user.market_id else None,
    )


@bp.route("/reviews/<int:review_id>/reply", methods=["POST"])
@vendor_only
def reply_review(review_id):
    review = Review.get_or_404(review_id)
    if review.vendor_id == current_user.user_id:
        review.reply = request.form.get("reply", "").strip()[:500] or None
        review.save()
        flash("Reply posted.", "success")
    return redirect(url_for("vendor.feedback"))


@bp.route("/reviews/<int:review_id>/flag", methods=["POST"])
@vendor_only
def flag_review(review_id):
    review = Review.get_or_404(review_id)
    if review.vendor_id == current_user.user_id:
        review.is_flagged = True
        review.save()
        flash("Review flagged for LGU moderation.", "info")
    return redirect(url_for("vendor.feedback"))


@bp.route("/profile")
@vendor_only
def profile():
    return render_template(
        "vendor_profile.html",
        scans=_weekly_scans(),
        market=Market.get(current_user.market_id) if current_user.market_id else None,
    )