from django.contrib import admin
from .models import AppliedDiscount, Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("name", "campaign_type", "kind", "value", "is_active", "start_time", "end_time")
    list_filter = ("campaign_type", "is_active")
    filter_horizontal = ("categories", "variants")


admin.site.register(AppliedDiscount)
