from flask import Blueprint, Response, abort
from flask_login import login_required

from utils import file_store

bp = Blueprint("files", __name__)


@bp.route("/p/<file_id>")
def public(file_id):
    """Public images (store cover photos) for visitors who aren't logged in."""
    if not file_id.isalnum() or len(file_id) != 32:
        abort(404)
    data, meta = file_store.load(file_store.PREFIX + file_id)
    if data is None or meta.get("kind") not in file_store.PUBLIC_KINDS             or not (meta.get("content_type") or "").startswith("image/"):
        abort(404)
    resp = Response(data, mimetype=meta["content_type"])
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<file_id>")
@login_required
def serve(file_id):
    """Serve an uploaded image (report photos, profile pictures) stored in Firestore.

    Only images are served here; generated reports go through their own
    permission-checked download routes."""
    if not file_id.isalnum() or len(file_id) != 32:
        abort(404)
    data, meta = file_store.load(file_store.PREFIX + file_id)
    if data is None or not (meta.get("content_type") or "").startswith("image/"):
        abort(404)
    resp = Response(data, mimetype=meta["content_type"])
    # File IDs never change content, so browsers can cache them for a long time.
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
