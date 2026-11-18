"""In-app notifications (bell icon, floating panel and live pop-ups)."""
from datetime import datetime

from models import DtiNotice, Notification, User


def notify(user_id, title, message="", link=None, icon="bell", tone="info"):
    """Create a notification. Never let a notification failure break the action that triggered it."""
    if not user_id:
        return None
    try:
        return Notification(user_id=user_id, title=title, message=message, link=link,
                            icon=icon, tone=tone).save()
    except Exception:
        return None


def notify_role(role, title, message="", link=None, icon="bell", tone="info"):
    """Notify every active account with this role (e.g. all DTI staff)."""
    try:
        users = [u for u in User.find(role=role) if u.is_active_flag]
    except Exception:
        return
    for u in users:
        notify(u.user_id, title, message, link=link, icon=icon, tone=tone)


def unread_count(user_id):
    return Notification.count(user_id=user_id, is_read=False)


def settle_dti_notices(listings, complete=False):
    """Mark pending DTI notices as complied once the vendor's price is back at or below the SRP,
    and tell the DTI officer who issued it. `listings`: {listing_id: Listing} to check; with
    complete=True it holds every listing, so a missing one was deleted (also counts as settled)."""
    settled = []
    for n in DtiNotice.find(status="issued"):
        if n.listing_id not in listings and not complete:
            continue
        l = listings.get(n.listing_id)
        if l is not None and l.price is not None and float(l.price) > float(n.srp):
            continue
        n.status, n.complied_at = "complied", datetime.utcnow()
        n.save()
        settled.append(n)
        vendor = User.get(n.vendor_id)
        who = (vendor.business_name or vendor.full_name) if vendor else "A vendor"
        notify(n.issued_by, f"{who} complied with notice #{n.notice_id}",
               f"{n.product_name} is now at or below the SRP of ₱{float(n.srp):,.2f}.",
               link="/dti/cases?tab=notices", icon="circle-check", tone="good")
    return settled
