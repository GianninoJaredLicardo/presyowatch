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
import qrcode


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


def report_verification_url(base_url: str, report_id: int) -> str:
    return f"{base_url.rstrip('/')}/lgu/reports/{report_id}"
