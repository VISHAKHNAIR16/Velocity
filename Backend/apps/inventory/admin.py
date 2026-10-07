from django.contrib import admin

from .models import Item


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "item_type", "item_code", "unit", "sales_price", "tax_rate", "current_stock", "is_active")
    list_filter = ("item_type", "unit", "tax_rate", "is_active")
    search_fields = ("name", "item_code", "barcode", "hsn_sac_code")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")