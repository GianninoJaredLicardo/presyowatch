"""PayMongo webhook: confirms payments even if the user closes the browser before returning."""
from flask import Blueprint, current_app, request

from models import Payment
from utils import paymongo
from utils.csrf import csrf_exempt

bp = Blueprint("payments", __name__)


@bp.route("/paymongo/webhook", methods=["POST"])
@csrf_exempt
def paymongo_webhook():
    if not paymongo.verify_webhook(request.get_data(), request.headers.get("Paymongo-Signature")):
        return {"error": "invalid signature"}, 401

    attrs = ((request.get_json(silent=True) or {}).get("data") or {}).get("attributes") or {}
    if attrs.get("type") == "checkout_session.payment.paid":
        checkout_id = (attrs.get("data") or {}).get("id")
        payment = Payment.first(checkout_id=checkout_id) if checkout_id else None
        if payment:
            from routes.lgu import finalize_payment
            try:
                finalize_payment(payment)  # re-checks with PayMongo; never trusts the event body alone
            except paymongo.PayMongoError:
                current_app.logger.exception("PayMongo webhook: could not confirm checkout %s", checkout_id)
                return {"error": "retry later"}, 500  # PayMongo retries failed deliveries
    return {"received": True}, 200
