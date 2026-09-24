# PresyoWatch

**A Real-Time Market Price Monitoring and Price Gouging Alert System for LGUs**
Capstone system for Cebu City & Lapu-Lapu City · Python (Flask) + Firebase Firestore

This is a working implementation of the core system described in your Chapter I
proposal: crowd-sourced price reporting, a DTI SRP baseline, automated price-spike
detection, an LGU verification workflow, public price search, price-trend charts,
PDF/CSV report generation for DTI/DA, QR codes, and an admin console with
analytics, vendor badges, and subscription (access-control) management.

## 1. Requirements

- Python 3.10+
- A Firebase project with Cloud Firestore enabled (Native mode)
- pip

## 2. Set up Firebase

1. In the [Firebase console](https://console.firebase.google.com), create a project and
   add **Firestore Database** (production mode).
2. Go to **Project settings → Service accounts → Generate new private key** and save the
   JSON file somewhere safe, **outside version control** (e.g. `firebase-key.json`).
3. Deploy the locked-down security rules (the Flask server uses the Admin SDK, which
   bypasses rules, so clients get no direct access):

   ```bash
   firebase deploy --only firestore:rules
   ```

   Or paste the contents of `firestore.rules` into the console's Rules tab.

Collections used: `users`, `markets`, `products`, `dti_srp`, `price_reports`, `alerts`,
`report_exports`, plus `counters` (auto-increment IDs). They are created on first write.
No composite indexes are required.

## 3. Configure environment variables

Create a `.env` file in the project root:

```
SECRET_KEY=change-this-to-something-random
FIREBASE_CREDENTIALS=firebase-key.json
```

If `FIREBASE_CREDENTIALS` is unset, Google Application Default Credentials are used
(e.g. when running on Cloud Run).

## 4. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 5. Seed Firestore and create the first admin account

```bash
python seed_data.py      # 4 markets, 10 commodities, starter DTI SRP baseline
python seed_admin.py
python seed_demo.py      # optional: demo resident, vendors, LGU staff, reports, alerts (password: demo1234)
```

Default login: `admin@presyowatch.local` / `Admin@123` — **change the password after first login** (via a future "change password" screen, or directly in the database for now).

## 6. Run the app

```bash
python app.py
```

Visit **http://localhost:5000**

## User roles & what they can do

The UI follows the Figma redesign: a mobile-first green "glass" theme with a bottom tab bar per role
(the app is centered at phone width on desktop; the admin console uses a wider layout).

| Role | How to get it | Tabs / capabilities |
|---|---|---|
| Resident / Consumer | Self-register (`/register`) | Home (prices, activity), Report a price (+ photo), Search prices, Price alerts (thresholds + latest spike), Profile, QR scanner |
| Market Vendor | Self-register with business details; LGU approves the verified badge | Home, Listings (add/edit/remove, stock status), Prices (update / bulk update), Feedback (reply / flag), Profile (public QR code + weekly scans) |
| LGU Staff | Request access at `/register/lgu`; admin approves | Home, Verify (approve/reject/flag with notes), Stats (7/30/90 days), Plan (subscription), Manage (vendors + alerts + report generator), Profile (QR codes, spike threshold, system logs) |
| DTI / DA | Created by admin (role `dti_da`) | Same app as LGU, plus reports that LGUs share via "Auto-send to DTI/DA" |
| Admin | Created via `seed_admin.py` | Wide console: analytics, users (approve, disable, reset password, vendor badge, subscription), DTI SRP baseline |

Public (no login): landing page, QR scanner (`/scan`), price search, and vendor pages (`/vendor/<id>`).

Notes:
- Vendor price updates for a listing linked to a tracked commodity are also filed as price reports, so
  the 20% spike rule and DTI SRP comparison apply to vendors too.
- Subscription payments are **simulated** (no gateway): renew/upgrade just record a billing entry, and only the
  last 4 card digits are stored.
- Email/SMS toggles in LGU settings are saved preferences; message delivery is not connected.
- "Forgot password" points users to an admin, who can issue a temporary password from the Users page.
- Generated reports are saved in `exports/` and downloaded from Manage → Reports.

## How the core features map to the code

| Capstone feature | Implementation |
|---|---|
| User registration for residents, LGUs, DTI, DA | `routes/auth.py`, role-based approval flow |
| Price reporting (product, price, location, date, photo) | `routes/resident.py: report_price()` |
| Real-time average price calculation per product/city | `utils/spike_detection.py: get_recent_average()` |
| Automated price spike detection (20% rule) | `utils/spike_detection.py: process_new_report()`, runs on every submission |
| Public price search | `routes/public.py: price_search()` |
| LGU dashboard (trends, alerts, verification queue) | `routes/lgu.py`, `templates/lgu_dashboard.html` |
| Report verification workflow | `routes/lgu.py: verify_report() / reject_report()` |
| Automated PDF/CSV report generation | `utils/report_generator.py`, `routes/lgu.py: reports_export()` |
| Price history charts | `routes/resident.py: price_history_data()` (Chart.js) |
| Admin analytics | `routes/admin.py: dashboard()` |
| QR code integration | `utils/qr_generator.py`, `routes/public.py: market_qr()`, `routes/lgu.py: qr_codes()` |
| SaaS-style subscription / access control | `User.subscription_status`, `routes/admin.py: update_subscription()` |
| Verified vendor badge | `routes/admin.py: verify_vendor()` |

## Notes on the DTI web-crawling module

The proposal calls for an automated crawler that pulls SRP bulletins from the
DTI website. Building a live scraper depends on the DTI site's current markup
and requires network access this environment doesn't have, so `dti_srp` is
implemented as a Firestore collection with a seeded baseline and an admin
screen (`/admin/srp`) to update it manually or via a scheduled script. To make
it fully automated later, write a scraper (e.g. with `requests` + `BeautifulSoup`)
that upserts rows into `dti_srp` on a cron job or Celery beat schedule — the
rest of the system (spike detection, dashboards, reports) will pick up new
values automatically since it always queries the latest bulletin_date.

## Project structure

```
presyowatch/
├── app.py                  # Flask app factory & entry point
├── config.py                # Configuration (Firebase credentials, spike threshold, etc.)
├── extensions.py             # Firestore client, login_manager singletons
├── models.py                 # Firestore document models
├── firestore.rules            # Deny-all client rules (server uses the Admin SDK)
├── seed_data.py                # Seeds markets, products, DTI SRP baseline
├── seed_admin.py               # Creates the first admin account
├── requirements.txt
├── routes/
│   ├── auth.py                # register / login / logout
│   ├── public.py               # home, price search, QR image
│   ├── resident.py              # submit reports, price history
│   ├── lgu.py                    # dashboard, verification, exports, QR posters
│   └── admin.py                   # analytics, user/vendor/SRP management
├── utils/
│   ├── spike_detection.py         # 20% rule-based gouging detection
│   ├── qr_generator.py             # QR code generation
│   └── report_generator.py          # PDF & CSV builders
├── templates/                        # Jinja2 + Bootstrap 5 UI
└── static/
    ├── css/style.css
    └── uploads/                        # resident-submitted photos
```

## Suggested next steps for your defense

- Add a "change password" / profile page.
- Add pagination to the verification queue and price search for larger datasets.
- Replace the manual SRP admin screen with a real scheduled scraper against DTI's public bulletins.
- Add unit tests for `utils/spike_detection.py` (the core algorithm examiners will likely ask about).
- Consider adding email/SMS notifications for high-severity alerts.
