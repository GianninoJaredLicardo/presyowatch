"""
PayMongo (https://paymongo.com) payments for LGU subscriptions.

Flow (Checkout Sessions API, no card data ever touches this server):
1. create_checkout() asks PayMongo for a hosted checkout page and returns its URL.
2. The user pays there (card, GCash, Maya, ...), then PayMongo sends them back.
3. The payment is confirmed by asking PayMongo directly (fetch_paid_details), on the
   return page and, if configured, from the webhook — whichever comes first.

Turned on by setting PAYMONGO_SECRET_KEY. Without it, payments stay simulated.
"""
import hashlib
import hmac

import requests
from flask import current_app

API = "https://api.paymongo.com/v1"
METHOD_LABELS = {
    "card": "Card", "gcash": "GCash", "paymaya": "Maya", "grab_pay": "GrabPay",
    "qrph": "QR Ph", "dob": "Online Banking", "dob_ubp": "Online Banking", "billease": "BillEase",
}


class PayMongoError(Exception):
    """Message is safe to show to the user."""


def enabled():
    return bool(current_app.config.get("PAYMONGO_SECRET_KEY"))


def _request(method, path, **kwargs):
    try:
        resp = requests.request(method, API + path, auth=(current_app.config["PAYMONGO_SECRET_KEY"], ""),
                                timeout=20, **kwargs)
    except requests.RequestException:
        raise PayMongoError("Couldn't reach PayMongo. Please check your connection and try again.")
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        detail = "; ".join(e.get("detail", "") for e in body.get("errors", []) if e.get("detail"))
        current_app.logger.error("PayMongo %s %s failed (%s): %s", method, path, resp.status_code, body)
        raise PayMongoError(f"PayMongo refused the request: {detail or resp.status_code}")
    return body["data"]


def create_checkout(*, amount_php, item_name, description, reference, success_url, cancel_url,
                    email=None, name=None, metadata=None):
    """Returns (checkout_session_id, checkout_url)."""
    methods = [m.strip() for m in current_app.config["PAYMONGO_METHODS"].split(",") if m.strip()]
    attributes = {
        "line_items": [{"currency": "PHP", "amount": int(round(amount_php * 100)), "name": item_name, "quantity": 1}],
        "payment_method_types": methods,
        "description": description,
        "reference_number": reference,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "show_description": True,
        "show_line_items": True,
        "send_email_receipt": bool(email),
        "metadata": {k: str(v) for k, v in (metadata or {}).items()},
    }
    if email or name:
        attributes["billing"] = {k: v for k, v in (("email", email), ("name", name)) if v}
    data = _request("POST", "/checkout_sessions", json={"data": {"attributes": attributes}})
    return data["id"], data["attributes"]["checkout_url"]


def fetch_paid_details(checkout_id):
    """None while unpaid; otherwise {"method", "label", "last4", "payment_id"} of the successful payment."""
    attrs = _request("GET", f"/checkout_sessions/{checkout_id}")["attributes"]
    for p in attrs.get("payments") or []:
        pa = p.get("attributes", {})
        if pa.get("status") == "paid":
            source = pa.get("source") or {}
            method = source.get("type") or "card"
            return {
                "method": method,
                "label": (source.get("brand") or "").title() or METHOD_LABELS.get(method, method.title()),
                "last4": source.get("last4"),
                "payment_id": p.get("id"),
            }
    intent = (attrs.get("payment_intent") or {}).get("attributes", {})
    if intent.get("status") == "succeeded":
        return {"method": None, "label": "PayMongo", "last4": None, "payment_id": None}
    return None


def verify_webhook(raw_body, signature_header):
    """Checks the Paymongo-Signature header ("t=...,te=...,li=...") against PAYMONGO_WEBHOOK_SECRET."""
    secret = current_app.config.get("PAYMONGO_WEBHOOK_SECRET")
    if not secret or not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    expected = hmac.new(secret.encode(), f"{parts.get('t', '')}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(parts.get(k) and hmac.compare_digest(parts[k], expected) for k in ("te", "li"))
