from .middleware import get_current_request
from .models import ActivityLog


def _client_ip(request):
    if not request:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


def log_activity(user, action, module, obj=None, description="", **metadata):
    """Single entry point for audit logging. Never raises into business code."""
    try:
        return ActivityLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action=action, module=module,
            object_type=obj.__class__.__name__ if obj is not None else "",
            object_id=str(getattr(obj, "pk", "") or ""),
            object_repr=str(obj)[:200] if obj is not None else "",
            description=description, metadata=metadata,
            ip_address=_client_ip(get_current_request()),
        )
    except Exception:       # audit must never break a sale
        return None
