"""
QR Code generation utility.

Two use cases from the capstone scope:
1. Consumer-facing QR: links to the public price-search page for a specific
   market so shoppers can scan a poster at the market entrance and instantly
   see current verified prices.
2. LGU verification QR: encodes a report_id so an LGU field officer can scan
   a vendor's printed slip / sticker and jump straight to that report's
   verification page.
"""
import io
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

import qrcode
from flask import current_app, request

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _lan_ip():
    """This computer's address on the local network (no traffic is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def public_base_url():
    """Base URL that phones scanning a QR code can actually open.

    1. PUBLIC_BASE_URL from the config/.env, when set (use this once the site is online).
    2. Otherwise the address the page was opened with — except "localhost"/"127.0.0.1",
       which only works on this computer, so it is swapped for the computer's Wi-Fi address.
    """
    configured = current_app.config.get("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/") + "/"
    parts = urlsplit(request.url_root)
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host not in LOCAL_HOSTS and "." in host and not _is_ip(host) \
            and not host.endswith(".local"):
        # Deployed behind a proxy that didn't say it was HTTPS: public sites are served over https,
        # and an http:// QR link may be blocked or redirected by phones.
        parts = parts._replace(scheme="https")
    if host in LOCAL_HOSTS:
        ip = _lan_ip()
        if ip:
            netloc = f"{ip}:{parts.port}" if parts.port else ip
            parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def is_local_network_url(url):
    """True when the URL only works for devices on the same network (e.g. 192.168.x.x)."""
    host = urlsplit(url).hostname or ""
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return host.lower() in LOCAL_HOSTS or host.endswith(".local")


def generate_qr_bytes(data: str) -> bytes:
    """Return PNG bytes for a QR code encoding `data`."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f5132", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def market_price_search_url(base_url: str, market_id: int) -> str:
    return f"{base_url.rstrip('/')}/prices/search?market_id={market_id}"


def vendor_qr_url(base_url: str, vendor_id: int) -> str:
    return f"{base_url.rstrip('/')}/vendor/{vendor_id}?src=qr"


def report_verification_url(base_url: str, report_id: int) -> str:
    return f"{base_url.rstrip('/')}/lgu/reports/{report_id}"
