import io
import csv
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, render_template, request, redirect, url_for, flash, Response
from flask_login import current_user

from models import DtiNotice, Listing, Market, Product, Review, ScanEvent
from utils.helpers import log_action, refresh_rules_for_product, role_required, vendor_stats
from utils.commodities import CATEGORIES
from utils.csv_import import parse_listing_file
from utils.notifications import notify, settle_dti_notices
from utils.qr_generator import is_local_network_url, public_base_url, vendor_qr_url
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
    if new_price == listing.price:
        return None
    listing.prev_price = listing.price
    listing.price = new_price
    listing.updated_at = datetime.utcnow()
    listing.save()
    settle_dti_notices({listing.listing_id: listing})
    if listing.product_id:
        refresh_rules_for_product(listing.product_id)
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
    by_id = {l.listing_id: l for l in listings}
    settle_dti_notices(by_id)  # notices whose price is back within SRP are marked complied
    dti_notices = [n for n in DtiNotice.find(vendor_id=current_user.user_id, status="issued") if n.listing_id in by_id]
    reviews = sorted(Review.find(vendor_id=current_user.user_id), key=lambda r: r.created_at, reverse=True)
    return render_template(
        "vendor_dashboard.html",
        listings=listings,
        needs_attention=[l for l in listings if l.stock_status != "in_stock"],
        dti_notices=dti_notices,
        flagged_items=[l for l in listings if l.is_price_flagged],
        # LGU price warnings from Flagged Vendors; one clears once the vendor updates that listing.
        warnings=[l for l in listings if l.lgu_warned_at and l.lgu_warned_at >= (l.updated_at or l.lgu_warned_at)],
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


# ---- CSV TEMPLATE DOWNLOAD ----
@bp.route("/listings/template")
@vendor_only
def download_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Item Name", "Category", "Price", "Unit", "Stock Status"])
    writer.writerow(["White Rice", "Grains", "52.00", "kg", "in_stock"])
    writer.writerow(["Red Onion", "Vegetables", "120.00", "kg", "low_stock"])
    
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=presyowatch_listing_template.csv"
    return response


# ---- CSV IMPORT ----
@bp.route("/listings/import", methods=["POST"])
@vendor_only
def import_listings():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Please choose a CSV or Excel file to import.", "danger")
        return redirect(url_for("vendor.listings"))
    if not file.filename.lower().endswith((".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")):
        flash(f"\"{file.filename}\" isn't a supported file. Please upload a CSV or Excel (.xlsx) file.", "danger")
        return redirect(url_for("vendor.listings"))

    try:
        rows, skipped, _cols = parse_listing_file(file.stream.read(), file.filename)
    except ValueError as e:
        flash(f"Couldn't import \"{file.filename}\". {e}", "danger")
        return redirect(url_for("vendor.listings"))
    except Exception:
        flash(f"Couldn't read \"{file.filename}\". Please check that the file isn't damaged and try again.", "danger")
        return redirect(url_for("vendor.listings"))

    imported = 0
    try:
        for r in rows:
            Listing(vendor_id=current_user.user_id, updated_at=datetime.utcnow(), **r).save()
            imported += 1
    except Exception:
        flash(f"Something went wrong while saving. Only {imported} of {len(rows)} "
              f"{_plural(len(rows), 'product')} were added. Please try again.", "danger")
        return redirect(url_for("vendor.listings"))

    log_action(current_user, "listings_imported", f"{file.filename}: added {imported}, skipped {len(skipped)}")
    flash(f"{imported} {_plural(imported, 'product')} added to your listings from \"{file.filename}\".", "success")
    if skipped:
        shown = "; ".join(skipped[:3]) + (f"; and {len(skipped) - 3} more" if len(skipped) > 3 else "")
        flash(f"{len(skipped)} {_plural(len(skipped), 'row')} {'was' if len(skipped) == 1 else 'were'} "
              f"not added because of missing or invalid details: {shown}.", "warning")

    return redirect(url_for("vendor.listings"))


def _plural(n, word):
    return word if n == 1 else word + "s"


@bp.route("/listings/new", methods=["GET", "POST"])
@bp.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@vendor_only
def listing_form(listing_id=None):
    listing = _own_listing(listing_id) if listing_id else None
    products = sorted(Product.all(), key=lambda p: p.product_name)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", type=float)
        category = request.form.get("category", "General").strip() or "General"
        unit = request.form.get("unit", "kg").strip() or "kg"
        stock = request.form.get("stock_status", "in_stock")
        
        # Commodity: chosen from the list (product_id), or typed in when it isn't listed ("new").
        choice = request.form.get("product_choice", "")
        product_name_input = request.form.get("product_name", "").strip() if choice == "new" else ""
        product_id = None
        if choice.isdigit():
            picked = Product.get(int(choice))
            product_id = picked.product_id if picked else None

        if product_name_input:
            # Find existing product (case-insensitive)
            existing_product = next((p for p in products if p.product_name.lower() == product_name_input.lower()), None)
            if existing_product:
                product_id = existing_product.product_id
            else:
                # Create a new product if it doesn't exist
                new_product = Product(product_name=product_name_input, unit="kg", category="General")
                new_product.save()
                product_id = new_product.product_id

        if not name or price is None or not price > 0 or stock not in STOCK:
            flash("Name and a valid price are required.", "danger")
            return redirect(request.url)

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
        listing.product_id = product_id  # Save the linked product ID
        listing.updated_at = datetime.utcnow()
        listing.save()

        if new_price is not None:
            _apply_price(listing, new_price)
        elif product_id and current_user.market_id:
            create_report(
                current_user.user_id, product_id, current_user.market_id, price,
                date.today(), vendor_name=current_user.business_name,
            )

        if product_id and new_price is None:
            refresh_rules_for_product(product_id)
        log_action(current_user, "listing_saved", name)
        flash("Listing saved.", "success")
        return redirect(url_for("vendor.listings"))

    # Get the current product name for the input field if editing
    current_product_name = ""
    if listing and listing.product_id:
        p = Product.get(listing.product_id)
        if p: current_product_name = p.product_name

    by_category = {}
    order = {c: i for i, c in enumerate(CATEGORIES)}
    for p in products:
        by_category.setdefault(p.category or "Other", []).append(p)
    grouped = sorted(by_category.items(), key=lambda kv: (order.get(kv[0], 99), kv[0].lower()))
    return render_template("listing_form.html", listing=listing, products=products, grouped=grouped,
                           categories=CATEGORIES, current_product_name=current_product_name)


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
        had_reply = bool(review.reply)
        review.reply = request.form.get("reply", "").strip()[:500] or None
        review.save()
        if review.reply and not had_reply:
            notify(review.user_id, f"{current_user.business_name or current_user.full_name} replied to your review",
                   review.reply[:160], link=f"/vendor/{current_user.user_id}", icon="reply")
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
        qr_target=vendor_qr_url(public_base_url(), current_user.user_id),
        qr_local_only=is_local_network_url(public_base_url()),
    )