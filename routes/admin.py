import secrets
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from models import User, PriceReport, Alert, Product, DtiSrp
from routes.auth import CITIES
from utils.helpers import log_action, read_name_fields
from utils.notifications import notify

bp = Blueprint("admin", __name__)

ROLES = ("resident", "vendor", "lgu_staff", "dti_da", "admin")
# Accounts an admin can create directly (residents and vendors self-register).
STAFF_ROLES = ("lgu_staff", "dti_da", "admin")
REPORT_STATUSES = ("pending", "verified", "rejected", "flagged")


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@bp.route("/")
@login_required
@admin_required
def index():
    return redirect(url_for("admin.applications"))


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


@bp.route("/users/create", methods=["POST"])
@login_required
@admin_required
def create_user():
    names, name_error = read_name_fields()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role")
    city = request.form.get("city") or None

    if name_error or not email:
        flash(name_error or "Email is required.", "danger")
        return redirect(url_for("admin.users"))
    if role not in STAFF_ROLES:
        abort(400)
    if city is not None and city not in CITIES:
        abort(400)
    if User.first(email=email):
        flash("An account with that email already exists.", "danger")
        return redirect(url_for("admin.users"))

    user = User(
        **names,
        email=email,
        role=role,
        city=city,
        subscription_status="active",
        is_active_flag=True,
        application_status="approved",
        org_name=request.form.get("org_name", "").strip() or None,
        division=request.form.get("division", "").strip() or None,
    )
    temp = secrets.token_urlsafe(8)
    user.set_password(temp)
    user.save()
    log_action(current_user, "create_user", f"Created {role} account for {email}")
    flash(f"Account created for {names['full_name']} ({email}). Temporary password: {temp} — share it securely and ask them to change it.", "warning")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def change_role(user_id):
    new_role = request.form.get("role")
    if new_role not in ROLES:
        abort(400)
    user = User.get_or_404(user_id)
    if user.user_id == current_user.user_id:
        flash("You cannot change your own role.", "danger")
        return redirect(url_for("admin.users"))
    old_role = user.role
    user.role = new_role
    user.save()
    log_action(current_user, "change_role", f"{user.email}: {old_role} -> {new_role}")
    flash(f"{user.full_name} is now {new_role.replace('_', ' ').title()}.", "success")
    return redirect(url_for("admin.users"))


def _is_pending_application(user):
    # Older sign-ups have no application_status; an inactive staff account without one is still pending.
    return user.role in ("lgu_staff", "dti_da") and not user.is_active_flag and         user.application_status in ("pending", None)


@bp.route("/applications")
@login_required
@admin_required
def applications():
    staff = [u for u in User.all() if u.role in ("lgu_staff", "dti_da")]
    pending = sorted((u for u in staff if _is_pending_application(u)), key=lambda u: u.created_at)
    rejected = sorted((u for u in staff if u.application_status == "rejected"),
                      key=lambda u: u.created_at, reverse=True)
    return render_template("admin_applications.html", pending=pending, rejected=rejected)


@bp.route("/users/<int:user_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_user(user_id):
    user = User.get_or_404(user_id)
    user.is_active_flag = True
    user.subscription_status = "active"
    if user.role in ("lgu_staff", "dti_da"):
        user.application_status = "approved"
    user.save()
    log_action(current_user, "approve_user", f"Approved {user.role} account {user.email}")
    flash(f"{user.full_name}'s account has been approved.", "success")
    return redirect(url_for("admin.applications" if request.form.get("back") == "applications" else "admin.users"))


@bp.route("/users/<int:user_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_user(user_id):
    user = User.get_or_404(user_id)
    if user.role not in ("lgu_staff", "dti_da"):
        abort(400)
    user.is_active_flag = False
    user.application_status = "rejected"
    user.save()
    log_action(current_user, "reject_user", f"Rejected {user.role} application from {user.email}")
    flash(f"{user.full_name}'s application has been rejected.", "info")
    return redirect(url_for("admin.applications"))


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
    notify(user.user_id, "You're now a Verified vendor",
           "Your Verified badge was approved. Shoppers will see it on your stall page.",
           link="/vendor/profile", icon="certificate", tone="good")
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
