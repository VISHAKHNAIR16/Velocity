"""Base admin class for tenant models."""

from django.contrib import admin


class TenantAdmin(admin.ModelAdmin):
    """Inherit for tenant models. Rows are created via the API, never from the admin."""

    list_filter = ("business",)
    readonly_fields = ("business", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False
