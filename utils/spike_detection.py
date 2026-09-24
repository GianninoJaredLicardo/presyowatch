"""
Automated Price Spike Detection module.

Implements Specific Objective 2.4 from the PresyoWatch capstone proposal:
a rule-based algorithm that flags a newly submitted price report when it is
unusually high compared to (a) the recent verified market average for that
product/city, and (b) the official DTI Suggested Retail Price (SRP).

A report is flagged when it exceeds either baseline by more than
`Config.SPIKE_THRESHOLD_PERCENT` (default 20%).
"""
from datetime import date, timedelta

from config import Config
from models import PriceReport, DtiSrp, Market, Product, Alert
from utils.helpers import get_settings


def get_recent_average(product_id, city, window_days, exclude_report_id=None):
    """Average of VERIFIED prices for a product within a city over the recent window."""
    since = date.today() - timedelta(days=window_days)
    markets = Market.index()
    prices = []
    for r in PriceReport.find(product_id=product_id, status="verified"):
        market = markets.get(r.market_id)
        if (
            r.report_date >= since
            and r.report_id != exclude_report_id
            and market is not None
            and market.city == city
        ):
            prices.append(float(r.price))
    return sum(prices) / len(prices) if prices else None


def get_latest_srp(product_id):
    entries = DtiSrp.find(product_id=product_id)
    if not entries:
        return None
    latest = max(entries, key=lambda s: (s.bulletin_date, s.srp_id))
    return float(latest.srp_price)


def evaluate_report(report, threshold_percent, window_days):
    """
    Compare a PriceReport's price against the recent market average and the
    DTI SRP baseline. Returns (is_spike, spike_percent, reason, baseline).
    """
    price = float(report.price)
    market = Market.get(report.market_id)

    recent_avg = get_recent_average(
        report.product_id, market.city, window_days, exclude_report_id=report.report_id
    )
    srp = get_latest_srp(report.product_id)

    baseline = recent_avg if recent_avg else srp
    baseline_label = "recent market average" if recent_avg else "DTI SRP"

    if baseline is None or baseline <= 0:
        return False, None, None, None

    percent_over = ((price - baseline) / baseline) * 100

    if percent_over >= threshold_percent:
        reason = (
            f"Reported price of PHP {price:,.2f} is {percent_over:.1f}% above the "
            f"{baseline_label} of PHP {baseline:,.2f}."
        )
        return True, round(percent_over, 2), reason, baseline

    return False, round(percent_over, 2), None, baseline


def process_new_report(report, threshold_percent, window_days):
    """
    Run spike detection on a freshly saved report (it must already have a
    report_id), update its flags, and create an Alert for LGU staff when needed.
    """
    is_spike, spike_percent, reason, baseline = evaluate_report(report, threshold_percent, window_days)

    report.is_spike = is_spike
    report.spike_percent = spike_percent

    if is_spike:
        report.status = "flagged"
        severity = "high" if spike_percent >= 50 else "medium"
        Alert(
            report_id=report.report_id,
            message=reason,
            severity=severity,
            product_name=Product.get(report.product_id).product_name,
            market_name=Market.get(report.market_id).market_name,
            reported_price=float(report.price),
            baseline=round(baseline, 2),
            spike_percent=spike_percent,
        ).save()

    report.save()
    return is_spike


def create_report(user_id, product_id, market_id, price, report_date, photo_path=None, vendor_name=None):
    """Save a new price report and run spike detection on it. Returns (report, is_spike)."""
    report = PriceReport(
        user_id=user_id,
        product_id=product_id,
        market_id=market_id,
        price=price,
        photo_path=photo_path,
        report_date=report_date,
        vendor_name=vendor_name,
        status="pending",
    ).save()
    threshold = get_settings()["spike_threshold"]
    return report, process_new_report(report, threshold, Config.RECENT_WINDOW_DAYS)
