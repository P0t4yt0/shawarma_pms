"""reports_analytics app: pre-aggregated history that makes reports, forecasting and promo suggestions cheap.

All tables are rebuilt/idempotently upserted by reports_analytics.services (nightly job or on demand), so
dashboards never scan the raw Order/OrderItem tables.

  DailySalesSummary     one row / day        -> daily revenue report, avg ticket time (kitchen KPI)
  HourlySales           one row / day / hour -> slow-period detection, targeted promo suggestions
  VariantDailySales     day x variant        -> top sellers, demand by menu variant
  IngredientDailyUsage  day x ingredient     -> perishable forecasting, spoilage tracking
  DemandForecast        ingredient x date    -> predicted usage (+ actual filled in later = accuracy)
  PromotionSuggestion   weekday/hour window  -> "run a 15% Happy Hour Tue 14-16" (accept -> creates Promotion)
"""
from django.conf import settings
from django.db import models


class DailySalesSummary(models.Model):
    date = models.DateField(unique=True)
    order_count = models.PositiveIntegerField(default=0)
    item_count = models.PositiveIntegerField(default=0)
    gross_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cancelled_count = models.PositiveIntegerField(default=0)
    avg_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    avg_ticket_seconds = models.PositiveIntegerField(null=True, blank=True)       # capture -> final bump
    avg_prep_seconds = models.PositiveIntegerField(null=True, blank=True)         # preparing -> ready
    late_ticket_count = models.PositiveIntegerField(default=0)                    # ticket > KDS_TARGET_TICKET_SECONDS
    payment_breakdown = models.JSONField(default=dict, blank=True)                # {"Cash": "1200.00", "GCash": ...}
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name_plural = "daily sales summaries"

    def __str__(self):
        return f"{self.date}: {self.net_sales}"


class HourlySales(models.Model):
    date = models.DateField()
    hour = models.PositiveSmallIntegerField()                    # 0-23, local time
    weekday = models.PositiveSmallIntegerField()                 # Mon=0 (denormalised for fast GROUP BY)
    order_count = models.PositiveIntegerField(default=0)
    net_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = [("date", "hour")]
        indexes = [models.Index(fields=["weekday", "hour"])]
        ordering = ["-date", "hour"]


class VariantDailySales(models.Model):
    date = models.DateField()
    variant = models.ForeignKey("point_of_sale.MenuVariant", on_delete=models.CASCADE, related_name="daily_sales")
    quantity = models.PositiveIntegerField(default=0)
    net_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = [("date", "variant")]
        indexes = [models.Index(fields=["variant", "date"])]


class IngredientDailyUsage(models.Model):
    date = models.DateField()
    ingredient = models.ForeignKey("inventory.Ingredient", on_delete=models.CASCADE, related_name="daily_usage")
    quantity_used = models.DecimalField(max_digits=12, decimal_places=3, default=0)     # via sales (net of reversals)
    quantity_wasted = models.DecimalField(max_digits=12, decimal_places=3, default=0)   # waste + spoilage
    ending_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = [("date", "ingredient")]
        indexes = [models.Index(fields=["ingredient", "date"])]


class DemandForecast(models.Model):
    ingredient = models.ForeignKey("inventory.Ingredient", on_delete=models.CASCADE, related_name="forecasts")
    forecast_date = models.DateField()
    predicted_usage = models.DecimalField(max_digits=12, decimal_places=3)
    method = models.CharField(max_length=40, default="same_weekday_avg")
    lookback_weeks = models.PositiveSmallIntegerField(default=6)
    actual_usage = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)   # back-filled
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("ingredient", "forecast_date", "method")]
        ordering = ["forecast_date", "ingredient__name"]


class PromotionSuggestion(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        DISMISSED = "dismissed", "Dismissed"

    weekday = models.PositiveSmallIntegerField()
    start_hour = models.PositiveSmallIntegerField()
    end_hour = models.PositiveSmallIntegerField()                # exclusive
    avg_orders_per_hour = models.DecimalField(max_digits=8, decimal_places=2)
    baseline_orders_per_hour = models.DecimalField(max_digits=8, decimal_places=2)
    gap_pct = models.DecimalField(max_digits=5, decimal_places=1)     # how far below the day's average
    suggested_percent_off = models.PositiveSmallIntegerField(default=10)
    reason = models.CharField(max_length=250)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    promotion = models.ForeignKey("promotions.Promotion", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Wd{self.weekday} {self.start_hour}:00-{self.end_hour}:00 -{self.suggested_percent_off}%"

    def accept(self, user):
        """Turn the suggestion into a live Happy-Hour promotion for that weekday window."""
        from datetime import time
        from promotions.models import Promotion
        promo = Promotion.objects.create(
            name=f"Slow-period deal {self.get_weekday_display_name()} {self.start_hour}-{self.end_hour}h",
            campaign_type=Promotion.Campaign.SLOW_PERIOD, kind=Promotion.Kind.PERCENT,
            value=self.suggested_percent_off, days_of_week=[self.weekday],
            start_time=time(self.start_hour), end_time=time(self.end_hour - 1, 59),
            created_by=user)
        self.status, self.promotion, self.reviewed_by = self.Status.ACCEPTED, promo, user
        self.save(update_fields=["status", "promotion", "reviewed_by"])
        return promo

    def get_weekday_display_name(self):
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][self.weekday]
