from django.contrib import admin
from .models import Category, MenuItem, MenuVariant, Order, OrderItem, OrderStatusLog


class VariantInline(admin.TabularInline):
    model = MenuVariant
    extra = 1


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    inlines = [VariantInline]


class ItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "status", "order_type", "total", "created_at", "ticket_seconds")
    list_filter = ("status", "order_type")
    inlines = [ItemInline]


admin.site.register(Category)
admin.site.register(OrderStatusLog)
