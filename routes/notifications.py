from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user, login_required

from models import Notification

bp = Blueprint("notifications", __name__)


def _mine():
    return sorted(Notification.find(user_id=current_user.user_id), key=lambda n: n.created_at, reverse=True)


@bp.route("/")
@login_required
def index():
    items = _mine()[:50]
    return render_template("notifications.html", items=items)


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
    return redirect(url_for("notifications.index"))
