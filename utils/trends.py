"""Public price trends for the landing page: every product's recent average, weekly change,
DTI SRP, and a daily price series. Built from LGU-verified price reports only."""
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from models import DtiSrp, Market, PriceReport, Product

HISTORY_DAYS = 90
_CACHE = {"at": 0.0, "data": None}
CACHE_SECONDS = 300  # the landing page is public; avoid re-reading every report on each visit


def _avg(values):
    return round(sum(values) / len(values), 2) if values else None


def _sparkline_points(series, width=120, height=34, pad=3):
    """SVG polyline points for the last 30 days of daily averages (None when < 2 points)."""
    if len(series) < 2:
        return None, None
    values = [v for _, v in series]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    first = date.fromisoformat(series[0][0])
    days = max(1, (date.fromisoformat(series[-1][0]) - first).days)
    pts = []
    for d, v in series:
        x = pad + (width - 2 * pad) * (date.fromisoformat(d) - first).days / days
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span) if hi != lo else height / 2
        pts.append((round(x, 1), round(y, 1)))
    return " ".join(f"{x},{y}" for x, y in pts), pts[-1]


def build_trends(force=False):
    if not force and _CACHE["data"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["data"]

    today = date.today()
    since = today - timedelta(days=HISTORY_DAYS - 1)
    week_ago = today - timedelta(days=6)
    two_weeks_ago = today - timedelta(days=13)

    products = sorted(Product.all(), key=lambda p: (p.category or "", p.product_name or ""))
    reports = [r for r in PriceReport.find(status="verified") if r.report_date and r.report_date >= since]

    srps = {}
    for s in DtiSrp.all():
        cur = srps.get(s.product_id)
        if cur is None or (s.bulletin_date, s.srp_id) > (cur.bulletin_date, cur.srp_id):
            srps[s.product_id] = s

    daily = defaultdict(lambda: defaultdict(list))  # product_id -> date -> [prices]
    for r in reports:
        daily[r.product_id][r.report_date].append(float(r.price))

    items = []
    for p in products:
        by_day = daily.get(p.product_id, {})
        series = [(d.isoformat(), _avg(v)) for d, v in sorted(by_day.items())]
        this_week = [x for d, v in by_day.items() if d >= week_ago for x in v]
        last_week = [x for d, v in by_day.items() if two_weeks_ago <= d < week_ago for x in v]
        current = _avg(this_week)
        if current is None and series:
            current = series[-1][1]  # nothing this week: show the latest known daily average
        previous = _avg(last_week)
        change = round(100 * (current - previous) / previous, 1) if current is not None and previous else None
        srp = float(srps[p.product_id].srp_price) if p.product_id in srps else None
        spark, spark_end = _sparkline_points([s for s in series if s[0] >= (today - timedelta(days=29)).isoformat()])
        items.append({
            "id": p.product_id, "name": p.product_name, "unit": p.unit or "unit",
            "category": (p.category or "General").title(),
            "current": current, "previous": previous, "change": change, "srp": srp,
            "above_srp": bool(srp and current and current > srp),
            "reports": sum(len(v) for v in by_day.values()),
            "last_date": series[-1][0] if series else None,
            "series": series, "spark": spark, "spark_end": spark_end,
        })

    # Products with data first, biggest weekly movers at the top.
    items.sort(key=lambda i: (i["current"] is None, -abs(i["change"] or 0), i["name"] or ""))

    month_ago = today - timedelta(days=29)
    data = {
        "items": items,
        "categories": sorted({i["category"] for i in items}),
        "stats": {
            "products": len(products),
            "with_data": sum(1 for i in items if i["current"] is not None),
            "reports_30d": sum(1 for r in reports if r.report_date >= month_ago),
            "markets": Market.count(),
            "spikes_7d": sum(1 for r in PriceReport.find(is_spike=True)
                             if r.report_date and r.report_date >= week_ago),
            "rising": sum(1 for i in items if (i["change"] or 0) > 0),
            "falling": sum(1 for i in items if (i["change"] or 0) < 0),
        },
        "updated": datetime.utcnow(),
        # Compact per-product series for the interactive chart (keys are strings for JSON).
        "chart": {str(i["id"]): {"name": i["name"], "unit": i["unit"], "category": i["category"],
                                 "srp": i["srp"], "series": i["series"]} for i in items},
    }
    _CACHE.update(at=time.time(), data=data)
    return data
