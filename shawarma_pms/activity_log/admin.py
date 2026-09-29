from django.contrib import admin
from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "module", "object_repr")
    list_filter = ("module", "action")
    search_fields = ("description", "object_repr", "user__username")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
