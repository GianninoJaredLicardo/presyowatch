from flask import Flask, request
from flask_login import current_user

from config import Config
from extensions import login_manager
from models import User
from utils.helpers import localtime, peso, timeago

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
}


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(int(user_id))

    from routes.auth import bp as auth_bp
    from routes.public import bp as public_bp
    from routes.resident import bp as resident_bp
    from routes.vendor import bp as vendor_bp
    from routes.lgu import bp as lgu_bp
    from routes.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(resident_bp, url_prefix="/resident")
    app.register_blueprint(vendor_bp, url_prefix="/vendor")
    app.register_blueprint(lgu_bp, url_prefix="/lgu")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    app.jinja_env.filters["timeago"] = timeago
    app.jinja_env.filters["localtime"] = localtime
    app.jinja_env.filters["peso"] = peso

    @app.context_processor
    def inject_globals():
        group = None
        if current_user.is_authenticated:
            group = {"lgu_staff": "lgu", "dti_da": "lgu"}.get(current_user.role, current_user.role)
        items = [
            {"label": label, "icon": icon, "endpoint": ep, "active": request.endpoint in (ep, *extra)}
            for label, icon, ep, extra in NAV.get(group, [])
        ]
        return {"nav_current_user": current_user, "nav_items": items}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
