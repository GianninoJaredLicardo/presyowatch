import secrets
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, current_app
)
from flask_login import login_user, logout_user, login_required, current_user

from models import Market, User
from utils.helpers import home_endpoint_for, log_action, read_name_fields

bp = Blueprint("auth", __name__)

# Self-registration may never grant "admin". Firestore has no ENUM constraint,
# so the allowed values are enforced here. "lgu" is only reachable through the
# separate staff portal (/staff/register); it covers LGU and DTI/DA staff.
REGISTER_ROLES = {"resident": "resident", "vendor": "vendor", "lgu": "lgu_staff"}
# Each group signs in through its own login page.
PORTALS = {
    "public": {"roles": ("resident", "vendor"), "endpoint": "auth.login", "template": "login.html"},
    "staff": {"roles": ("lgu_staff", "dti_da"), "endpoint": "auth.staff_login", "template": "staff_login.html"},
    "dti": {"roles": ("dti_da",), "endpoint": "auth.dti_login", "template": "dti_login.html"}, # NEW
    "admin": {"roles": ("admin",), "endpoint": "auth.admin_login", "template": "admin_login.html"},
}
CITIES = ("Cebu City", "Lapu-Lapu City")


def _clean(field):
    """Helper to clean string inputs."""
    return request.form.get(field, "").strip()


# =============================================================
# REGISTRATION ROUTES
# =============================================================

@bp.route("/register", methods=["GET", "POST"])
def register():
    """Single-page registration form for Resident and Vendor."""
    return _register("public")


@bp.route("/register/<role>", methods=["GET", "POST"])
def register_form(role):
    if role == "lgu":
        return redirect(url_for("auth.staff_register"))
    if role not in REGISTER_ROLES:
        return redirect(url_for("auth.register"))
    return _register(role)


@bp.route("/staff/register", methods=["GET", "POST"])
def staff_register():
    return _register("lgu")


def _register(role):
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))

    markets = sorted(Market.all(), key=lambda m: m.market_name)

    if request.method == "POST":
        names, name_error = read_name_fields()
        email = _clean("email").lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        contact_number = _clean("contact_number")
        city = request.form.get("city") or None
        market_id = request.form.get("market_id", type=int)
        business_name = _clean("business_name")
        business_address = _clean("business_address")
        org_name = _clean("org_name")
        
        back = redirect(url_for("auth.staff_register") if role == "lgu" else url_for("auth.register_form", role=role))

        if name_error:
            flash(name_error, "danger")
            return back
        if role == "vendor":
            market = Market.get(market_id or 0)
            if market is None or not business_name:
                flash("Please enter your business name and choose your market.", "danger")
                return back
            city = market.city
        elif city not in CITIES:
            flash("Please choose your city.", "danger")
            return back
        if not email or not password:
            flash("Please fill out all required fields.", "danger")
            return back

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return back

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return back

        if User.first(email=email):
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("auth.login"))

        # LGU and DTI/DA staff share one sign-up form and need admin approval before they can log in.
        needs_approval = role == "lgu"
        account_role = REGISTER_ROLES.get(role, "resident")
        
        if needs_approval and request.form.get("agency") == "dti_da":
            account_role = "dti_da"

        user = User(
            **names,
            email=email,
            role=account_role,
            city=city,
            contact_number=contact_number,
            subscription_status="trial",
            is_active_flag=not needs_approval,
            address=_clean("address") or None,
            business_name=business_name or None,
            business_address=business_address or None,
            market_id=market_id if role == "vendor" else None,
            vendor_status="pending" if role == "vendor" else "active",
            org_name=org_name or None,
            division=_clean("division") or None,
            application_status="pending" if needs_approval else None,
        )
        user.set_password(password)
        user.save()
        log_action(user, "register", f"New {account_role} account")

        if needs_approval:
            flash("Account created. An administrator must approve LGU / DTI-DA accounts before you can log in.", "info")
            return redirect(url_for("auth.staff_login"))
            
        flash("Account created successfully. You may now log in.", "success")
        return redirect(url_for("auth.login"))

    # Options for the floating-label dropdowns: (value, text)
    options = {
        "markets": markets, "role": role,
        "city_options": [(c, c) for c in CITIES],
        "markets_options": [(m.market_id, m.market_name, m.city or "") for m in markets],
    }
    if role == "public":
        return render_template("register.html", **options)
    elif role == "lgu":
        return render_template("register_lgu.html", **options)
    else:
        return render_template("register_form.html", **options)


# Alias so your old landing page links (`url_for('auth.register_continue')`) still work
bp.add_url_rule(
    "/register/<role>/form",
    endpoint="register_continue",
    view_func=register_form,
    methods=["GET", "POST"],
)


# =============================================================
# LOGIN / LOGOUT
# =============================================================

@bp.route("/login", methods=["GET", "POST"])
def login():
    return _login("public")


@bp.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    return _login("staff")


@bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    return _login("admin")


def _login(portal):
    """Residents/vendors sign in at /login, LGU and DTI/DA staff at /staff/login, admins at /admin/login."""
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))
        
    here = url_for(PORTALS[portal]["endpoint"], next=request.args.get("next") or None)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        login_role = request.form.get("login_as", "").strip() or None

        user = User.first(email=email)

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return redirect(here)

        if user.role not in PORTALS[portal]["roles"]:
            right = next(k for k, v in PORTALS.items() if user.role in v["roles"])
            flash({"public": "This is not a staff account. Please use the regular login.",
                   "staff": "Government staff accounts sign in through the staff portal.",
                   "admin": "Administrator accounts sign in through the admin login."}[right], "warning")
            return redirect(url_for(PORTALS[right]["endpoint"]))

        if portal == "public" and login_role != user.role:
            label = "Market Vendor" if user.role == "vendor" else "Resident/Consumer"
            flash(f"This is a {label} account. Please choose '{label}' under Login As.", "warning")
            return redirect(here)

        if not user.is_active_flag:
            if user.role == "vendor" and user.vendor_status == "suspended":
                flash("Your vendor account has been suspended. Please contact your LGU.", "danger")
            elif user.application_status == "rejected":
                flash("Your access request was not approved. Please contact the PresyoWatch administrator.", "danger")
            else:
                flash("Your account is pending administrator approval.", "warning")
            return redirect(here)

        login_user(user)
        log_action(user, "login")
        flash(f"Welcome back, {user.full_name}!", "success")
        
        next_url = request.args.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for(home_endpoint_for(user)))

    return render_template(PORTALS[portal]["template"])


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.home"))


# =============================================================
# PASSWORD / ACCOUNT
# =============================================================

@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("auth.change_password"))

        if new != confirm:
            flash("New passwords do not match.", "danger")
            return redirect(url_for("auth.change_password"))

        if len(new) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for("auth.change_password"))

        current_user.set_password(new)
        current_user.save()
        flash("Password updated successfully.", "success")
        return redirect(url_for("public.home"))

    return render_template("change_password.html")


@bp.route("/edit-account", methods=["GET", "POST"])
@login_required
def edit_account():
    markets = sorted(Market.all(), key=lambda m: m.market_name)
    
    if request.method == "POST":
        names, name_error = read_name_fields()
        email = _clean("email").lower()
        if name_error or not email:
            flash(name_error or "Email is required.", "danger")
            return redirect(url_for("auth.edit_account"))
        other = User.first(email=email)
        if other and other.user_id != current_user.user_id:
            flash("That email is already used by another account.", "danger")
            return redirect(url_for("auth.edit_account"))

        city = request.form.get("city") or None
        if city is not None and city not in CITIES:
            flash("Invalid city selected.", "danger")
            return redirect(url_for("auth.edit_account"))

        for field, value in names.items():
            setattr(current_user, field, value)
        current_user.email = email
        current_user.contact_number = _clean("contact_number") or None
        current_user.address = _clean("address") or None
        
        if current_user.role == "vendor":
            market = Market.get(request.form.get("market_id", type=int) or 0)
            if market is None or not _clean("business_name"):
                flash("Business name and market are required.", "danger")
                return redirect(url_for("auth.edit_account"))
            current_user.business_name = _clean("business_name")
            current_user.business_address = _clean("business_address") or None
            current_user.market_id = market.market_id
            current_user.city = market.city
        else:
            current_user.city = city
            
        if current_user.role in ("lgu_staff", "dti_da"):
            current_user.org_name = _clean("org_name") or None
            current_user.division = _clean("division") or None
            current_user.contact_person = _clean("contact_person") or None
            
        current_user.save()
        log_action(current_user, "edit_profile")
        flash("Profile updated.", "success")
        return redirect(url_for("public.home"))

    return render_template("edit_account.html", markets=markets)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash("If your email is registered, you will receive a reset link.", "info")
        return redirect(url_for("auth.login"))
    return render_template("forgot_password.html")

@bp.route("/dti/login", methods=["GET", "POST"])
def dti_login():
    """Dedicated login page for DTI/DA staff."""
    return _login("dti")