from django.contrib.auth.models import Group
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def ensure_role_groups(sender, **kwargs):
    """Make sure the three role groups exist after any migrate (permissions assigned by setup_roles)."""
    if sender.name != "security":
        return
    from .models import ROLE_GROUP_NAMES
    for name in ROLE_GROUP_NAMES.values():
        Group.objects.get_or_create(name=name)
