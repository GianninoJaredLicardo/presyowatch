from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from models import Market, User
from utils.helpers import home_endpoint_for, log_action

bp = Blueprint("auth", __name__)

# Self-registration may never grant "admin". Firestore has no ENUM constraint,
# so the allowed values are enforced here.
REGISTER_ROLES = {"resident": "resident", "vendor": "vendor", "lgu": "lgu_staff"}
CITIES = ("Cebu City", "Lapu-Lapu City")


def _clean(name):
    return request.form.get(name, "").strip()


@bp.route("/register")
def register():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))
    return render_template("register.html")


@bp.route("/register/continue")
def register_continue():
    role = request.args.get("role", "resident")
    if role not in REGISTER_ROLES:
        role = "resident"
    return redirect(url_for("auth.register_form", role=role))


@bp.route("/register/<role>", methods=["GET", "POST"])
def register_form(role):
    if role not in REGISTER_ROLES:
        return redirect(url_for("auth.register"))
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))

    markets = sorted(Market.all(), key=lambda m: m.market_name)

    if request.method == "POST":
        full_name = _clean("full_name")
        email = _clean("email").lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        contact_number = _clean("contact_number")
        city = request.form.get("city") or None
        market_id = request.form.get("market_id", type=int)
        business_name = _clean("business_name")
        business_address = _clean("business_address")
        org_name = _clean("org_name")
        back = redirect(url_for("auth.register_form", role=role))

        if not full_name or not email or not password:
            flash("Please fill out all required fields.", "danger")
            return back
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return back
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return back
        if city is not None and city not in CITIES:
            flash("Invalid city selected.", "danger")
            return back
        if role == "vendor":
            market = Market.get(market_id) if market_id else None
            if not business_name or market is None:
                flash("Business name and market location are required for vendors.", "danger")
                return back
            city = market.city
            if not request.form.get("agree"):
                flash("You must agree to the Terms and Conditions.", "danger")
                return back
        if role == "lgu" and not org_name:
            flash("Organization name is required.", "danger")
            return back
        if User.first(email=email):
            flash("An account with that email already exists.", "danger")
            return back

        # LGU staff accounts need admin approval before they can log in.
        needs_approval = role == "lgu"
        user = User(
            full_name=full_name,
            email=email,
            role=REGISTER_ROLES[role],
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
        )
        user.set_password(password)
        user.save()
        log_action(user, "register", f"New {REGISTER_ROLES[role]} account")

        if needs_approval:
            flash("Account created. An administrator must approve LGU accounts before you can log in.", "info")
        else:
            flash("Account created successfully. You may now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register_form.html", role=role, markets=markets)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        login_as = request.form.get("login_as", "resident")

        user = User.first(email=email)
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("auth.login"))

        if user.role in ("resident", "vendor") and login_as != user.role:
            label = "Market Vendor" if user.role == "vendor" else "Resident/Consumer"
            flash(f"This is a {label} account. Please choose '{label}' under Login As.", "warning")
            return redirect(url_for("auth.login"))

        if not user.is_active_flag:
            if user.role == "vendor" and user.vendor_status == "suspended":
                flash("Your vendor account has been suspended. Please contact your LGU.", "danger")
            else:
                flash("Your account is pending administrator approval.", "warning")
            return redirect(url_for("auth.login"))

        login_user(user)
        log_action(user, "login")
        flash(f"Welcome back, {user.full_name}!", "success")
        return redirect(url_for(home_endpoint_for(user)))

    return render_template("login.html")


@bp.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.home"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "danger")
        elif new != confirm:
            flash("New passwords do not match.", "danger")
        else:
            current_user.set_password(new)
            current_user.save()
            log_action(current_user, "change_password")
            flash("Password updated.", "success")
            return redirect(url_for(home_endpoint_for(current_user)))
        return redirect(url_for("auth.change_password"))
    return render_template("change_password.html")


@bp.route("/account/edit", methods=["GET", "POST"])
@login_required
def edit_account():
    markets = sorted(Market.all(), key=lambda m: m.market_name)
    user = current_user

    if request.method == "POST":
        full_name = _clean("full_name")
        email = _clean("email").lower()
        if not full_name or not email:
            flash("Name and email are required.", "danger")
            return redirect(url_for("auth.edit_account"))
        other = User.first(email=email)
        if other and other.user_id != user.user_id:
            flash("That email is already used by another account.", "danger")
            return redirect(url_for("auth.edit_account"))

        city = request.form.get("city") or None
        if city is not None and city not in CITIES:
            flash("Invalid city selected.", "danger")
            return redirect(url_for("auth.edit_account"))

        user.full_name = full_name
        user.email = email
        user.contact_number = _clean("contact_number") or None
        user.address = _clean("address") or None
        if user.role == "vendor":
            market = Market.get(request.form.get("market_id", type=int) or 0)
            if market is None or not _clean("business_name"):
                flash("Business name and market are required.", "danger")
                return redirect(url_for("auth.edit_account"))
            user.business_name = _clean("business_name")
            user.business_address = _clean("business_address") or None
            user.market_id = market.market_id
            user.city = market.city
        else:
            user.city = city
        if user.role in ("lgu_staff", "dti_da"):
            user.org_name = _clean("org_name") or None
            user.division = _clean("division") or None
            user.contact_person = _clean("contact_person") or None
        user.save()
        log_action(user, "edit_profile")
        flash("Profile updated.", "success")
        return redirect(url_for(_profile_endpoint(user)))

    return render_template("edit_account.html", markets=markets)


def _profile_endpoint(user):
    return {"vendor": "vendor.profile", "lgu_staff": "lgu.profile", "dti_da": "lgu.profile"}.get(
        user.role, "resident.profile"
    )
