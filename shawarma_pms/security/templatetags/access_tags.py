# security/templatetags/access_tags.py
from django import template
from django.utils import timezone
from security.models import TemporaryAccess

register = template.Library()

@register.filter(name='has_dynamic_access')
def has_dynamic_access(user, perm_string):
    if user.is_superuser:
        return True
        
    # 1. Check permanent permission (via Groups like Cashier/Manager)
    if user.has_perm(perm_string):
        return True
        
    # 2. Check temporary override
    try:
        app_label, codename = perm_string.split('.')
        has_temp = TemporaryAccess.objects.filter(
            employee=user,
            permission__content_type__app_label=app_label,
            permission__codename=codename,
            expires_at__gt=timezone.now()
        ).exists()
        return has_temp
    except ValueError:
        return False