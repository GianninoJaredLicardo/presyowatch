from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import Notification
from utils.helpers import timeago

bp = Blueprint("notifications", __name__)


def _mine():
    return sorted(Notification.find(user_id=current_user.user_id), key=lambda n: n.created_at, reverse=True)


@bp.route("/")
@login_required
def index():
    items = _mine()[:50]
    return render_template("notifications.html", items=items)


@bp.route("/feed")
@login_required
def feed():
    """Latest notifications for the floating panel and live pop-ups (polled by static/js/notifications.js)."""
    items = _mine()
    return jsonify({
        "unread": sum(1 for n in items if not n.is_read),
        "items": [{
            "id": n.notification_id, "title": n.title, "message": n.message or "", "icon": n.icon or "bell",
            "tone": n.tone or "info", "unread": not n.is_read, "when": timeago(n.created_at),
            "url": url_for("notifications.open_notification", notification_id=n.notification_id),
        } for n in items[:15]],
    })


@bp.route("/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    n = Notification.get_or_404(notification_id)
    if n.user_id != current_user.user_id:
        abort(404)
    if not n.is_read:
        n.is_read = True
        n.save()
    link = n.link or ""
    return redirect(link if link.startswith("/") and not link.startswith("//") else url_for("notifications.index"))


@bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    for n in Notification.find(user_id=current_user.user_id, is_read=False):
        n.is_read = True
        n.save()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"unread": 0})
    return redirect(request.referrer or url_for("notifications.index"))
