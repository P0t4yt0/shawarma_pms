"""promotions app: Happy Hour, BOGO, seasonal & manual discounts, applied automatically at the POS.

The engine (promotions/engine.py) reads these rules; AppliedDiscount stores WHAT was applied to each order
so analytics can measure promo lift. Slow-period suggestions live in reports_analytics.PromotionSuggestion.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Promotion(models.Model):
    class Campaign(models.TextChoices):
        HAPPY_HOUR = "happy_hour", "Happy Hour"
        BOGO = "bogo", "Buy One Get One"
        SEASONAL = "seasonal", "Seasonal"
        SLOW_PERIOD = "slow_period", "Slow-period booster"
        MANUAL = "manual", "Manual (cashier applied)"

    class Kind(models.TextChoices):
        PERCENT = "percent", "Percent off"
        FIXED = "fixed", "Fixed amount off (per unit)"
        BOGO = "bogo", "Buy X get Y"

    class Scope(models.TextChoices):
        ALL = "all", "Whole menu"
        CATEGORIES = "categories", "Selected categories"
        VARIANTS = "variants", "Selected variants"

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, blank=True, help_text="Optional code for manual promos")
    campaign_type = models.CharField(max_length=20, choices=Campaign.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                help_text="Percent (0-100) or peso amount; for BOGO = % off the 'get' units (100 = free)")
    buy_quantity = models.PositiveSmallIntegerField(default=1)      # BOGO: buy X ...
    get_quantity = models.PositiveSmallIntegerField(default=1)      # ... get Y discounted

    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.ALL)
    categories = models.ManyToManyField("point_of_sale.Category", blank=True, related_name="promotions")
    variants = models.ManyToManyField("point_of_sale.MenuVariant", blank=True, related_name="promotions")

    # --- schedule
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    days_of_week = models.JSONField(default=list, blank=True, help_text="[0..6] Mon=0; empty = every day")
    start_time = models.TimeField(null=True, blank=True, help_text="Happy Hour window start")
    end_time = models.TimeField(null=True, blank=True)

    # --- rules
    min_order_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    max_discount = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=10, help_text="Lower number wins when not stackable")
    is_stackable = models.BooleanField(default=False)
    auto_apply = models.BooleanField(default=True, help_text="False = only when a cashier selects it")
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name

    # ---- rule checks
    def is_live(self, at=None):
        """Is the promo valid at this moment (date range, weekday, time window, usage cap)?"""
        at = timezone.localtime(at or timezone.now())
        if not self.is_active:
            return False
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return False
        if self.start_date and at.date() < self.start_date:
            return False
        if self.end_date and at.date() > self.end_date:
            return False
        if self.days_of_week and at.weekday() not in self.days_of_week:
            return False
        if self.start_time and self.end_time:
            now_t = at.time()
            if self.start_time <= self.end_time:
                return self.start_time <= now_t <= self.end_time
            return now_t >= self.start_time or now_t <= self.end_time      # window crosses midnight
        return True

    def covers(self, variant):
        if self.scope == self.Scope.ALL:
            return True
        if self.scope == self.Scope.CATEGORIES:
            return self.categories.filter(pk=variant.item.category_id).exists()
        return self.variants.filter(pk=variant.pk).exists()


class AppliedDiscount(models.Model):
    """Audit + analytics: which promotion took how much off which order line."""
    order = models.ForeignKey("point_of_sale.Order", on_delete=models.CASCADE, related_name="applied_discounts")
    order_item = models.ForeignKey("point_of_sale.OrderItem", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="applied_discounts")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="applications")
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    description = models.CharField(max_length=160, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.promotion} -{self.amount} on {self.order}"
