import json

import firebase_admin
from firebase_admin import credentials, firestore
from flask_login import LoginManager

from config import Config

login_manager = LoginManager()
login_manager.login_view = "auth.login"
# Admin pages send logged-out visitors to the admin login; LGU/DTI-DA pages to the staff portal.
login_manager.blueprint_login_views = {"admin": "auth.admin_login", "lgu": "auth.staff_login"}
login_manager.login_message_category = "warning"

_client = None


def get_db():
    """Return the shared Firestore client, initializing Firebase on first use."""
    global _client
    if _client is None:
        try:
            firebase_admin.get_app()
        except ValueError:
            if Config.FIREBASE_CREDENTIALS_JSON:
                # Hosting platforms: paste the whole service-account JSON into an env var.
                cred = credentials.Certificate(json.loads(Config.FIREBASE_CREDENTIALS_JSON))
            elif Config.FIREBASE_CREDENTIALS:
                cred = credentials.Certificate(Config.FIREBASE_CREDENTIALS)
            else:
                cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)
        _client = firestore.client()
    return _client
