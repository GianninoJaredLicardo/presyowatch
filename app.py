from flask import Flask, request
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import login_manager
from models import User
from utils.helpers import localtime, peso, split_full_name, timeago

# (label, FontAwesome icon, endpoint, other endpoints that keep the tab highlighted)
NAV = {
    "resident": [
        ("Home", "house", "resident.dashboard", ()),
        ("Report", "clipboard-list", "resident.report_price", ()),
        ("Search", "magnifying-glass", "public.price_search", ()),
        ("Alert", "bell", "resident.alerts", ()),
        ("Profile", "user", "resident.profile", ("auth.edit_account", "auth.change_password")),
    ],
    "vendor": [
        ("Home", "house", "vendor.dashboard", ()),
        ("Listings", "list-check", "vendor.listings", ("vendor.listing_form",)),
        ("Prices", "coins", "vendor.prices", ("vendor.bulk_prices",)),
        ("Feedback", "star", "vendor.feedback", ()),
        ("Profile", "user", "vendor.profile", ("auth.edit_account", "auth.change_password")),
    ],
    "lgu": [
        ("Home", "house", "lgu.dashboard", ()),
        ("Verify", "circle-check", "lgu.verification_queue", ("lgu.report_detail",)),
        ("Stats", "chart-column", "lgu.stats", ()),
        ("Plan", "file-invoice", "lgu.subscription", ()),
        ("Manage", "store", "lgu.manage", ()),
        ("Profile", "user", "lgu.profile", ("lgu.logs", "lgu.qr_codes", "auth.edit_account", "auth.change_password")),
    ],
    "dti": [
        ("Home", "house", "dti.dashboard", ()),
        ("SRP", "tags", "dti.srp", ()),
        ("Monitor", "scale-balanced", "dti.monitor", ()),
        ("Cases", "gavel", "dti.cases", ()),
        ("Reports", "file-lines", "dti.reports", ()),
        ("Profile", "user", "dti.profile", ("auth.edit_account", "auth.change_password")),
    ],
}


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if app.config["SECRET_KEY"] == "presyowatch-dev-secret-change-me":
        if app.config["PUBLIC_BASE_URL"]:
            raise RuntimeError("SECRET_KEY is not set. Set a long random SECRET_KEY environment variable before deploying.")
        app.logger.warning("Using the default development SECRET_KEY. Set SECRET_KEY before deploying.")

    # Hosting platforms put the app behind an HTTPS proxy; trust its headers so
    # generated links (QR codes, redirects) use the real https:// address.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    login_manager.init_app(app)

    from utils.csrf import init_csrf
    init_csrf(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(int(user_id))

    from routes.auth import bp as auth_bp
    from routes.public import bp as public_bp
    from routes.resident import bp as resident_bp
    from routes.vendor import bp as vendor_bp
    from routes.lgu import bp as lgu_bp
    from routes.admin import bp as admin_bp
    from routes.notifications import bp as notifications_bp
    from routes.files import bp as files_bp
    from routes.payments import bp as payments_bp
    from routes.dti import bp as dti_bp
    from utils.file_store import file_url

    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(resident_bp, url_prefix="/resident")
    app.register_blueprint(vendor_bp, url_prefix="/vendor")
    app.register_blueprint(lgu_bp, url_prefix="/lgu")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(files_bp, url_prefix="/files")
    app.register_blueprint(payments_bp, url_prefix="/payments")
    app.register_blueprint(dti_bp, url_prefix="/dti")
    app.jinja_env.globals["file_url"] = file_url

    app.jinja_env.filters["timeago"] = timeago
    app.jinja_env.filters["localtime"] = localtime
    app.jinja_env.filters["peso"] = peso
    app.jinja_env.globals["split_full_name"] = split_full_name

    @app.context_processor
    def inject_globals():
        group = None
        if current_user.is_authenticated:
            group = {"lgu_staff": "lgu", "dti_da": "dti"}.get(current_user.role, current_user.role)
        items = [
            {"label": label, "icon": icon, "endpoint": ep, "active": request.endpoint in (ep, *extra)}
            for label, icon, ep, extra in NAV.get(group, [])
        ]
        pending_applications = 0
        if group == "admin":
            from routes.admin import _is_pending_application
            pending_applications = sum(
                1 for u in User.find(("role", "in", ["lgu_staff", "dti_da"])) if _is_pending_application(u)
            )
        unread_notifications = 0
        if group in ("resident", "vendor", "dti"):
            from utils.notifications import unread_count
            try:
                unread_notifications = unread_count(current_user.user_id)
            except Exception:
                unread_notifications = 0
        return {"nav_current_user": current_user, "nav_items": items, "pending_applications": pending_applications,
                "unread_notifications": unread_notifications,
                "show_bell": group in ("resident", "vendor", "dti")}

    return app


app = create_app()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
