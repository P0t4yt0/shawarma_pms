from django.contrib import admin
from .models import Ingredient, LowStockAlert, Recipe, RecipeLine, StockMovement


class RecipeLineInline(admin.TabularInline):
    model = RecipeLine
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    inlines = [RecipeLineInline]


@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "current_stock", "unit", "reorder_level")
    list_filter = ("category",)


admin.site.register(StockMovement)
admin.site.register(LowStockAlert)
