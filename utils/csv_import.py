"""
Best-effort product import from arbitrary CSV files.

Vendors upload whatever they have: POS exports, inventory sheets, price lists
typed in Excel, files with title rows above the header, no header at all,
semicolon/tab separators, peso signs in prices, etc. This module figures out
which column is the item name and which is the price (plus optional category,
unit and stock), and pulls out every row it can make sense of.

Entry point: parse_listing_csv(raw_bytes) -> (rows, skipped, info)
"""
import csv
import io
import re
from statistics import median

STOCK = ("in_stock", "low_stock", "out_of_stock")
MAX_ROWS = 5000
HEADER_SCAN_ROWS = 15

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")
_UNIT_RE = re.compile(
    r"(?:/|\bper\b|\bpor\b|\ba\b)\s*(kg|kilo|g|gram|grams|l|liter|litre|ml|pc|pcs|piece|pack|pck|bottle|can|sack|"
    r"dozen|doz|tray|bundle|tali|box|bag|sachet|lb)\b",
    re.I,
)
_SUMMARY_RE = re.compile(r"^\s*(grand\s+)?(sub\s*)?total\b|^\s*(sum|overall)\s*[:\-]?\s*$", re.I)

# Keyword -> score for each role. Negative scores veto/penalise.
_ROLE_KEYWORDS = {
    "price": {
        "price": 4, "srp": 4, "presyo": 4, "retail": 3, "selling": 3, "sell": 2, "msrp": 4, "mrp": 4,
        "rate": 2, "cost": 2, "amount": 1, "php": 2, "₱": 3, "peso": 2, "pesos": 2, "halaga": 3,
        "total": -3, "subtotal": -4, "value": -2, "discount": -4, "tax": -4, "vat": -4,
        "dealer": -1, "reseller": -1, "wholesale": -1, "old": -2, "previous": -2, "prev": -2,
        "qty": -4, "quantity": -4,
    },
    "name": {
        "item": 4, "items": 4, "product": 4, "products": 4, "name": 3, "description": 3, "desc": 3,
        "commodity": 4, "commodities": 4, "goods": 3, "particulars": 4, "article": 3, "title": 2,
        "merchandise": 3, "produkto": 4, "pangalan": 3, "paninda": 4, "bilihin": 4,
        "code": -5, "id": -5, "sku": -5, "barcode": -5, "no": -3, "number": -3, "category": -5,
        "type": -3, "brand": -2, "supplier": -5, "vendor": -5, "store": -5, "customer": -5, "unit": -5,
        "price": -5,
    },
    "category": {
        "category": 5, "categories": 5, "kategorya": 5, "type": 3, "group": 3, "class": 3,
        "classification": 4, "department": 3, "dept": 3, "section": 3, "uri": 3,
        "price": -5, "code": -2, "id": -3,
    },
    "unit": {
        "unit": 4, "units": 4, "uom": 5, "measure": 3, "measurement": 3, "yunit": 4,
        "price": -6, "cost": -6, "srp": -6, "count": -3, "qty": -3, "quantity": -3,
    },
    "stock": {
        "stock": 4, "stocks": 4, "qty": 4, "quantity": 4, "available": 3, "availability": 4,
        "inventory": 3, "onhand": 4, "status": 2, "balance": 2, "remaining": 3, "count": 2,
        "value": -6, "price": -6, "level": -4, "reorder": -6, "min": -4, "minimum": -4, "sold": -3,
    },
    "low_level": {
        "reorder": 5, "minimum": 2, "min": 2, "threshold": 4, "alert": 2,
    },
}
_ROLE_ORDER = ("name", "price", "category", "unit", "stock", "low_level")


def _decode(raw):
    # utf-8-sig strips the BOM Excel adds; cp1252 covers Excel "CSV (Comma delimited)" with ñ, ₱, etc.
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_rows(text):
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        # Pick whichever common delimiter appears most consistently.
        counts = {d: sample.count(d) for d in ",;\t|"}
        delim = max(counts, key=counts.get) if any(counts.values()) else ","
        dialect = type("Guessed", (csv.excel,), {"delimiter": delim})
    rows = []
    for row in csv.reader(io.StringIO(text, newline=""), dialect):
        rows.append([(c or "").strip() for c in row])
        if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
            break
    return rows


def _tokens(header):
    h = (header or "").replace("﻿", "").lower()
    toks = re.findall(r"₱|[a-z]+", h)
    if "low" in toks and ("level" in toks or "stock" in toks) and "price" not in toks:
        toks.append("reorder")  # "Low-stock level", "Low stock alert"
    if "on" in toks and "hand" in toks:
        toks.append("onhand")
    return toks


def _role_scores(header):
    toks = _tokens(header)
    scores = {}
    for role, kw in _ROLE_KEYWORDS.items():
        pos = [kw[t] for t in toks if kw.get(t, 0) > 0]
        neg = [kw[t] for t in toks if kw.get(t, 0) < 0]
        if pos:
            scores[role] = max(pos) + sum(neg)
    return scores


def parse_number(s):
    """Pull the first number out of messy text: '₱1,200.00', 'PHP 52', '52/kg', '45 - 50'."""
    if s is None:
        return None
    m = _NUM_RE.search(str(s).replace(" ", " "))
    if not m:
        return None
    num = m.group()
    if re.fullmatch(r"-?\d+,\d{1,2}", num):
        num = num.replace(",", ".")  # "58,00" / "12,5" -> comma used as the decimal point
    try:
        return float(num.replace(",", ""))
    except ValueError:
        return None


def _is_numeric_cell(s):
    if not s:
        return False
    stripped = re.sub(r"[₱$,\s]|php|php\.|p(?=\d)", "", s.lower())
    stripped = re.sub(r"/?\s*(kg|g|pc|pcs|l|ml|pack)\.?$", "", stripped)
    try:
        float(stripped)
        return True
    except ValueError:
        return False


def _unit_from(text):
    m = _UNIT_RE.search(text or "")
    if not m:
        return None
    u = m.group(1).lower()
    return {"kilo": "kg", "gram": "g", "grams": "g", "liter": "L", "litre": "L", "l": "L",
            "pcs": "pc", "piece": "pc", "pck": "pack", "doz": "dozen"}.get(u, u)


def parse_stock(value, low_level=""):
    """Accept a status word ('In stock', 'Sold out', 'Low', 'Yes') or a quantity number."""
    v = (value or "").strip().lower()
    if not v:
        return "in_stock"
    norm = v.replace("-", "_").replace(" ", "_")
    if norm in STOCK:
        return norm
    qty = parse_number(v) if _is_numeric_cell(v) else None
    if qty is not None:
        low = parse_number(low_level)
        if qty <= 0:
            return "out_of_stock"
        if low is not None and qty <= low:
            return "low_stock"
        return "in_stock"
    if any(w in v for w in ("out", "sold", "none", "unavail", "wala", "ubos", "n/a")) or v in ("no", "0", "x", "false"):
        return "out_of_stock"
    if any(w in v for w in ("low", "few", "limited", "konti", "kaunti")):
        return "low_stock"
    return "in_stock"


def _find_header(rows):
    """Return (header_index or None, {role: col}) using keyword matching on the first rows."""
    best_idx, best_score, best_cols = None, 0, {}
    for idx, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if not any(row):
            continue
        # A header row is mostly text.
        filled = [c for c in row if c]
        if sum(_is_numeric_cell(c) for c in filled) > len(filled) / 2:
            continue
        cols, total = _assign_roles(row)
        if total > best_score and ("name" in cols or "price" in cols):
            best_idx, best_score, best_cols = idx, total, cols
    if best_score >= 3:
        return best_idx, best_cols
    return None, {}


def _assign_roles(header):
    candidates = []  # (score, role, col)
    for i, h in enumerate(header):
        for role, score in _role_scores(h).items():
            if score > 0:
                candidates.append((score, -i, role, i))
    candidates.sort(reverse=True)
    cols, used, total = {}, set(), 0
    for score, _, role, i in candidates:
        if role in cols or i in used:
            continue
        cols[role] = i
        used.add(i)
        total += score
    return cols, total


def _column_profile(data, i):
    vals = [r[i] for r in data if i < len(r) and r[i]]
    if not vals:
        return {"filled": 0, "numeric": 0.0, "distinct": 0, "decimals": 0.0, "median": 0, "avg_len": 0}
    nums = [parse_number(v) for v in vals if _is_numeric_cell(v)]
    return {
        "filled": len(vals) / max(len(data), 1),
        "numeric": len(nums) / len(vals),
        "distinct": len(set(v.lower() for v in vals)) / len(vals),
        "decimals": sum(1 for v in vals if re.search(r"\.\d", v)) / len(vals),
        "median": median(nums) if nums else 0,
        "positive": sum(1 for n in nums if n and n > 0) / len(vals),
        "avg_len": sum(len(v) for v in vals) / len(vals),
        "currency": sum(1 for v in vals if re.search(r"₱|php|^p\d", v, re.I)) / len(vals),
    }


def _infer_by_content(data, cols, header):
    """Fill in name/price from cell contents when headers didn't tell us."""
    width = max((len(r) for r in data), default=0)
    used = set(cols.values())
    profiles = {i: _column_profile(data, i) for i in range(width)}
    header = header or []

    # Validate header-chosen columns against their contents.
    if "price" in cols and profiles.get(cols["price"], {}).get("numeric", 0) < 0.5:
        used.discard(cols.pop("price"))
    if "name" in cols and profiles.get(cols["name"], {}).get("numeric", 1) > 0.5:
        used.discard(cols.pop("name"))

    if "name" not in cols:
        best, best_score = None, 0
        for i, p in profiles.items():
            if i in used or p["filled"] < 0.3 or p["numeric"] > 0.3:
                continue
            # Item names: text, mostly unique, reasonably long. Penalise code-like columns.
            h_scores = _role_scores(header[i]) if i < len(header) else {}
            if h_scores.get("name", 0) < 0 and len(h_scores) == 1:
                continue
            score = p["distinct"] * 2 + min(p["avg_len"], 30) / 10 + p["filled"]
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            cols["name"] = best
            used.add(best)

    if "price" not in cols:
        best, best_score = None, 0
        for i, p in profiles.items():
            if i in used or p["numeric"] < 0.6 or p.get("positive", 0) < 0.5:
                continue
            h = _role_scores(header[i]) if i < len(header) else {}
            if h.get("stock", 0) > 0 or h.get("low_level", 0) > 0:
                continue
            # Prices tend to have centavos / peso signs; quantities and codes tend to be whole numbers.
            score = 1 + p["decimals"] * 2 + p["currency"] * 3 + (0.5 if p["median"] >= 5 else 0) - (1 if p["distinct"] > 0.98 and p["decimals"] == 0 and p["median"] > 1000 else 0)
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            cols["price"] = best
            used.add(best)

    if "unit" not in cols:
        for i, p in profiles.items():
            if i in used or p["numeric"] > 0.2 or p["avg_len"] > 8 or p["filled"] < 0.3:
                continue
            vals = [r[i].lower() for r in data[:200] if i < len(r) and r[i]]
            hits = sum(1 for v in vals if _unit_from("/" + v) or v in ("kg", "g", "l", "ml", "pc", "pcs", "pack"))
            if vals and hits / len(vals) >= 0.6:
                cols["unit"] = i
                used.add(i)
                break
    return cols


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _read_xlsx_sheets(raw):
    try:
        import openpyxl
    except ImportError:
        raise ValueError("Excel import isn't available on this server. Save the file as CSV and try again.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Couldn't open this Excel file. Try saving it again as .xlsx or .csv.")
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append([_cell_text(v) for v in r])
            if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
                break
        sheets.append(rows)
    wb.close()
    return sheets


def parse_listing_file(raw, filename="", default_category="General"):
    """Import from .csv/.tsv/.txt or Excel .xlsx/.xlsm. For workbooks, uses the sheet with the most products."""
    name = (filename or "").lower()
    if name.endswith(".xls"):
        raise ValueError("Old .xls Excel files aren't supported. In Excel, use File > Save As > .xlsx or CSV.")
    if name.endswith((".xlsx", ".xlsm")) or raw[:2] == b"PK":
        best, last_err = None, None
        for sheet_rows in _read_xlsx_sheets(raw):
            try:
                result = _parse_rows(sheet_rows, default_category)
            except ValueError as e:
                last_err = e
                continue
            if best is None or len(result[0]) > len(best[0]):
                best = result
        if best is None:
            raise last_err or ValueError("The Excel file is empty.")
        return best
    return parse_listing_csv(raw, default_category)


def parse_listing_csv(raw, default_category="General"):
    """
    Returns (rows, skipped, info):
      rows    - list of dicts with name, category, price, unit, stock_status
      skipped - list of human-readable reasons for rows that were dropped
      info    - dict describing which columns were used (for user feedback)
    Raises ValueError with a user-facing message when nothing usable is found.
    """
    return _parse_rows(_read_rows(_decode(raw)), default_category)


def _parse_rows(rows, default_category="General"):
    if not any(any(r) for r in rows):
        raise ValueError("The CSV file is empty.")

    header_idx, cols = _find_header(rows)
    header = rows[header_idx] if header_idx is not None else []
    data_start = header_idx + 1 if header_idx is not None else 0
    data = [r for r in rows[data_start:data_start + MAX_ROWS] if any(r)]

    if not data:
        raise ValueError("The file has column headers but no product rows.")

    cols = _infer_by_content(data, cols, header)
    if "name" not in cols or "price" not in cols:
        missing = " and ".join(x for role, x in (("name", "an item name"), ("price", "a price")) if role not in cols)
        raise ValueError(f"Couldn't find {missing} column in this file. Make sure it has one column with "
                         "product names and one with prices.")

    # No keyword header found, but the first row is text where prices should be
    # (e.g. "Col A,Col B" or headers in another language): treat it as the header.
    if header_idx is None:
        first = next((i for i, r in enumerate(rows) if any(r)), None)
        if first is not None:
            fr = rows[first]
            p = cols["price"]
            if p < len(fr) and fr[p] and not _is_numeric_cell(fr[p]):
                header_idx, header = first, fr
                data_start = first + 1

    def cell(row, role):
        i = cols.get(role)
        return row[i] if i is not None and i < len(row) else ""

    price_header = header[cols["price"]] if header else ""
    header_unit = _unit_from(price_header) or (re.search(r"\((kg|g|pc|pcs|l|ml|pack)\)", price_header, re.I) or [None, None])[1]
    has_unit_col = "unit" in cols

    out, skipped, seen_header = [], [], [h.lower() for h in header]
    for offset, row in enumerate(rows[data_start:data_start + MAX_ROWS]):
        line_no = data_start + offset + 1
        if not any(row):
            continue
        if seen_header and [c.lower() for c in row] == seen_header:
            continue  # repeated header (multi-page exports)
        name = cell(row, "name").strip()
        raw_price = cell(row, "price")
        if not name and not raw_price:
            continue
        if _SUMMARY_RE.search(name) or (not name and _SUMMARY_RE.search(" ".join(row))):
            continue
        filled = [c for c in row if c]
        if len(filled) == 1 and not raw_price and (len(name) > 60 or re.match(r"(note|source|remarks?|\*)", name, re.I)):
            continue  # footnote / remark line, not a product
        if not name:
            skipped.append(f"row {line_no} has no item name")
            continue
        if _is_numeric_cell(name):
            skipped.append(f"row {line_no} has a number ('{name}') instead of an item name")
            continue
        price = parse_number(raw_price)
        if price is None or price <= 0:
            skipped.append(f"{name} (row {line_no}) has " + (f"an invalid price '{raw_price}'" if raw_price else "no price"))
            continue

        unit = cell(row, "unit") or _unit_from(raw_price) or header_unit or ("kg" if has_unit_col else "pc")
        out.append({
            "name": name[:120],
            "category": (cell(row, "category") or default_category)[:60],
            "price": round(price, 2),
            "unit": unit.lower()[:20] if unit != "L" else unit,
            "stock_status": parse_stock(cell(row, "stock"), cell(row, "low_level")),
        })

    if not out:
        raise ValueError("No products could be read from this file. "
                         + (f"First problem: {skipped[0]}." if skipped else ""))

    info = {role: (header[i] if header and i < len(header) and header[i] else f"column {i + 1}")
            for role, i in cols.items()}
    return out, skipped, info
