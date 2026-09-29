"""activity_log app: append-only audit trail (who did what, when, from where)."""
from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        STATUS_CHANGE = "status_change", "Order status change"
        STOCK_ADJUST = "stock_adjust", "Stock adjustment"
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"
        VOID = "void", "Void"
        CLOCK_IN = "clock_in", "Clock in"
        CLOCK_OUT = "clock_out", "Clock out"
        PROMO = "promo", "Promotion"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="activity_logs")
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    module = models.CharField(max_length=40, db_index=True)          # app label
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["module", "-created_at"]),
                   models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.user} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("ActivityLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("ActivityLog is append-only.")
