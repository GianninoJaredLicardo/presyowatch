"""
Store uploaded photos and generated reports in Firestore, so they survive
redeploys/restarts on hosting platforms whose disks are wiped.

Firestore documents are capped at 1 MiB, so:
- photos are resized/re-encoded to a compact JPEG first (also strips EXIF,
  which can contain the phone's GPS location), and
- any file larger than one chunk is split across several chunk documents.

Stored references look like "db:<file_id>". Older records that point at files
on disk (e.g. "abc.jpg" or "uploads/profile/x.jpg") still work via file_url().
"""
import io
import uuid
from datetime import datetime

from flask import url_for

from extensions import get_db

PREFIX = "db:"
CHUNK_BYTES = 900 * 1024
MAX_PHOTO_SIDE = 1280


class InvalidImage(ValueError):
    pass


def _files():
    return get_db().collection("files")


def _chunks():
    return get_db().collection("file_chunks")


def save_bytes(data, content_type, owner_id=None, kind="file", filename=None):
    """Save raw bytes; returns a "db:<id>" reference."""
    file_id = uuid.uuid4().hex
    parts = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)] or [b""]
    batch = get_db().batch()
    for n, part in enumerate(parts):
        batch.set(_chunks().document(f"{file_id}_{n}"), {"file_id": file_id, "n": n, "data": part})
    batch.set(_files().document(file_id), {
        "file_id": file_id, "content_type": content_type, "size": len(data), "chunks": len(parts),
        "owner_id": owner_id, "kind": kind, "filename": filename, "created_at": datetime.utcnow(),
    })
    batch.commit()
    return PREFIX + file_id


def save_photo(file_storage, owner_id=None, kind="photo", max_side=MAX_PHOTO_SIDE):
    """Validate, shrink and save an uploaded image. Raises InvalidImage if it isn't a picture."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(file_storage.stream)
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise InvalidImage("That file isn't a valid image.")
    img = ImageOps.exif_transpose(img)  # keep phone photos the right way up
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.convert("RGBA").split()[-1])
        img = bg
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True, progressive=True)  # no EXIF written
    return save_bytes(out.getvalue(), "image/jpeg", owner_id=owner_id, kind=kind)


def is_stored(ref):
    return isinstance(ref, str) and ref.startswith(PREFIX)


def get_meta(ref):
    if not is_stored(ref):
        return None
    snap = _files().document(ref[len(PREFIX):]).get()
    return snap.to_dict() if snap.exists else None


def load(ref):
    """Return (bytes, meta) or (None, None) if missing."""
    meta = get_meta(ref)
    if not meta:
        return None, None
    fid = meta["file_id"]
    parts = get_db().get_all([_chunks().document(f"{fid}_{n}") for n in range(meta["chunks"])])
    by_n = {s.get("n"): s.get("data") for s in parts if s.exists}
    if len(by_n) != meta["chunks"]:
        return None, None
    return b"".join(by_n[n] for n in range(meta["chunks"])), meta


def delete(ref):
    meta = get_meta(ref)
    if not meta:
        return
    batch = get_db().batch()
    for n in range(meta["chunks"]):
        batch.delete(_chunks().document(f"{meta['file_id']}_{n}"))
    batch.delete(_files().document(meta["file_id"]))
    batch.commit()


# Kinds of images anyone may see without logging in (shoppers scanning a stall's QR code).
PUBLIC_KINDS = {"store_cover"}


def public_file_url(ref):
    """URL for an image in PUBLIC_KINDS that works for visitors who aren't logged in."""
    if not is_stored(ref):
        return ""
    return url_for("files.public", file_id=ref[len(PREFIX):])


def file_url(ref, legacy_prefix=""):
    """URL for an image/file reference: Firestore-stored or a legacy file under /static."""
    if not ref:
        return ""
    if is_stored(ref):
        return url_for("files.serve", file_id=ref[len(PREFIX):])
    return url_for("static", filename=legacy_prefix + ref)
