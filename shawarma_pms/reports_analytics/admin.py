from django.contrib import admin
from .models import (DailySalesSummary, DemandForecast, HourlySales, IngredientDailyUsage, PromotionSuggestion,
                     VariantDailySales)


@admin.action(description="Accept selected suggestions (create promotions)")
def accept(modeladmin, request, queryset):
    for s in queryset.filter(status="pending"):
        s.accept(request.user)


@admin.register(PromotionSuggestion)
class PromotionSuggestionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "gap_pct", "status", "reason")
    actions = [accept]


for m in (DailySalesSummary, HourlySales, VariantDailySales, IngredientDailyUsage, DemandForecast):
    admin.site.register(m)
