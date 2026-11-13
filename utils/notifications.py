"""In-app notifications for residents and vendors (shown under the bell icon)."""
from models import Notification


def notify(user_id, title, message="", link=None, icon="bell", tone="info"):
    """Create a notification. Never let a notification failure break the action that triggered it."""
    if not user_id:
        return None
    try:
        return Notification(user_id=user_id, title=title, message=message, link=link,
                            icon=icon, tone=tone).save()
    except Exception:
        return None


def unread_count(user_id):
    return Notification.count(user_id=user_id, is_read=False)
