"""Admin for the purchases app."""

from django.contrib import admin

from .models import Purchase, PurchaseItem, PurchaseReturn


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    readonly_fields = [
        "gross_amount",
        "net_amount",
        "taxable_value",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "total_amount",
    ]


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    date_hierarchy = "purchase_date"
    list_display = [
        "purchase_date",
        "bill_number",
        "party",
        "status",
        "grand_total",
        "place_of_supply",
    ]
    list_filter = ["status", "supply_type", "purchase_date"]
    search_fields = ["bill_number", "party__name", "supplier_gstin"]
    readonly_fields = [
        "business",
        "supplier_name",
        "supplier_gstin",
        "business_name",
        "business_gstin",
        "subtotal",
        "taxable_total",
        "cgst_total",
        "sgst_total",
        "igst_total",
        "grand_total",
        "created_at",
        "updated_at",
    ]
    inlines = [PurchaseItemInline]


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    date_hierarchy = "return_date"
    list_display = ["return_date", "return_number", "purchase", "grand_total", "status"]
    list_filter = ["status", "return_date"]
    search_fields = ["return_number", "purchase__bill_number"]
    readonly_fields = [
        "business",
        "grand_total",
        "cgst_total",
        "sgst_total",
        "igst_total",
        "created_at",
        "updated_at",
    ]
