from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .services import log_activity


@receiver(user_logged_in)
def on_login(sender, request, user, **kw):
    log_activity(user, "login", "security", user, "User logged in")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kw):
    log_activity(user, "logout", "security", user, "User logged out")
