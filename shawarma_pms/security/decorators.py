# security/decorators.py
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from functools import wraps
from .models import TemporaryAccess

def require_dynamic_access(perm_string):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.is_superuser or request.user.has_perm(perm_string):
                return view_func(request, *args, **kwargs)
                
            try:
                app_label, codename = perm_string.split('.')
                has_temp = TemporaryAccess.objects.filter(
                    employee=request.user,
                    permission__content_type__app_label=app_label,
                    permission__codename=codename,
                    expires_at__gt=timezone.now()
                ).exists()
                
                if has_temp:
                    return view_func(request, *args, **kwargs)
            except ValueError:
                pass
                
            raise PermissionDenied("Your access has expired or you do not have permission.")
        return _wrapped_view
    return decorator