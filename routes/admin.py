import secrets
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from models import User, PriceReport, Alert, Product, DtiSrp

bp = Blueprint("admin", __name__)

ROLES = ("resident", "vendor", "lgu_staff", "dti_da", "admin")
REPORT_STATUSES = ("pending", "verified", "rejected", "flagged")


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@bp.route("/dashboard")
@login_required
@admin_required
def dashboard():
    by_role = [(role, User.count(role=role)) for role in ROLES]
    by_status = [(s, PriceReport.count(status=s)) for s in REPORT_STATUSES]
    by_role = [(k, n) for k, n in by_role if n]
    by_status = [(k, n) for k, n in by_status if n]

    return render_template(
        "admin_dashboard.html",
        total_users=User.count(),
        total_reports=PriceReport.count(),
        total_alerts=Alert.count(),
        open_alerts=Alert.count(is_resolved=False),
        pending_approvals=User.count(is_active_flag=False),
        role_labels=[r[0] for r in by_role],
        role_values=[r[1] for r in by_role],
        status_labels=[s[0] for s in by_status],
        status_values=[s[1] for s in by_status],
    )


@bp.route("/users")
@login_required
@admin_required
def users():
    all_users = sorted(User.all(), key=lambda u: u.created_at, reverse=True)
    return render_template("admin_users.html", users=all_users)


@bp.route("/users/<int:user_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_user(user_id):
    user = User.get_or_404(user_id)
    user.is_active_flag = True
    user.subscription_status = "active"
    user.save()
    flash(f"{user.full_name}'s account has been approved.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@admin_required
def toggle_active(user_id):
    user = User.get_or_404(user_id)
    user.is_active_flag = not user.is_active_flag
    user.save()
    flash(f"{user.full_name}'s account is now {'active' if user.is_active_flag else 'disabled'}.", "info")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def reset_password(user_id):
    user = User.get_or_404(user_id)
    temp = secrets.token_urlsafe(8)
    user.set_password(temp)
    user.save()
    flash(f"Temporary password for {user.full_name}: {temp} — share it securely and ask them to change it.", "warning")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/verify-vendor", methods=["POST"])
@login_required
@admin_required
def verify_vendor(user_id):
    user = User.get_or_404(user_id)
    user.is_verified_vendor = True
    user.vendor_status = "active"
    user.vendor_verified_at = datetime.utcnow()
    user.save()
    flash(f"{user.full_name} is now a verified vendor.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/subscriptions/<int:user_id>/<string:new_status>", methods=["POST"])
@login_required
@admin_required
def update_subscription(user_id, new_status):
    if new_status not in ("active", "inactive", "trial"):
        abort(400)
    user = User.get_or_404(user_id)
    user.subscription_status = new_status
    user.save()
    flash(f"Subscription for {user.full_name} set to {new_status}.", "info")
    return redirect(url_for("admin.users"))


@bp.route("/srp", methods=["GET", "POST"])
@login_required
@admin_required
def srp_baseline():
    """
    Manual stand-in admin screen for the DTI SRP baseline table. In
    production this is refreshed automatically by the web-crawling module
    (see utils/dti_scraper.py); this screen lets an admin update it in the
    meantime or for products the crawler hasn't covered yet.
    """
    if request.method == "POST":
        product_id = request.form.get("product_id", type=int)
        srp_price = request.form.get("srp_price", type=float)
        bulletin_date_str = request.form.get("bulletin_date") or str(date.today())
        bulletin_date = date.fromisoformat(bulletin_date_str)

        if not product_id or Product.get(product_id) is None or srp_price is None or not srp_price > 0:
            flash("A valid product and SRP price are required.", "danger")
            return redirect(url_for("admin.srp_baseline"))

        DtiSrp(
            product_id=product_id,
            srp_price=srp_price,
            bulletin_date=bulletin_date,
            source="Manual admin update",
        ).save()
        flash("DTI SRP baseline updated.", "success")
        return redirect(url_for("admin.srp_baseline"))

    products = sorted(Product.all(), key=lambda p: p.product_name)
    latest_srp = {}
    for srp in DtiSrp.all():
        current = latest_srp.get(srp.product_id)
        if current is None or (srp.bulletin_date, srp.srp_id) > (current.bulletin_date, current.srp_id):
            latest_srp[srp.product_id] = srp
    for p in products:
        latest_srp.setdefault(p.product_id, None)
    return render_template("admin_srp.html", products=products, latest_srp=latest_srp)
