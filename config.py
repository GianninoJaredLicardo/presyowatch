import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "presyowatch-dev-secret-change-me")

    # Path to the Firebase service-account JSON key. If unset, Google
    # Application Default Credentials are used (e.g. on Cloud Run).
    FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS")

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    EXPORT_FOLDER = os.path.join(BASE_DIR, "exports")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB max photo upload

    # Business rule: flag a report as a possible price spike / gouging
    # when it exceeds the recent market average (or DTI SRP) by this ratio.
    SPIKE_THRESHOLD_PERCENT = 20.0

    # Number of days considered "recent" when computing the rolling
    # market average used for spike detection.
    RECENT_WINDOW_DAYS = 14


# LGU subscription plans (payments are simulated; no real gateway is connected).
PLANS = {
    "monthly": {
        "name": "Monthly", "price": 999, "days": 30, "period": "month", "markets": 5,
        "features": ["Full price monitoring", "Up to 5 markets", "PDF/CSV export",
                     "Vendor management", "Unlimited reports", "Automated alerts", "LGU dashboard"],
        "excluded": ["Priority support", "Custom branding"],
    },
    "quarterly": {
        "name": "Quarterly", "price": 2499, "days": 91, "period": "quarter", "markets": 10,
        "features": ["Full price monitoring", "Up to 10 markets", "PDF/CSV export",
                     "Vendor management", "Unlimited reports", "Automated alerts",
                     "LGU dashboard", "Priority support"],
        "excluded": ["Custom branding"],
    },
    "annual": {
        "name": "Annual", "price": 8999, "days": 365, "period": "year", "markets": None,
        "features": ["Full price monitoring", "Unlimited markets", "PDF/CSV export",
                     "Vendor management", "Unlimited reports", "Automated alerts",
                     "LGU dashboard", "Priority support", "Custom branding"],
        "excluded": [],
    },
}
