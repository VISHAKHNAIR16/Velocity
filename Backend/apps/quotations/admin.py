"""Admin for the quotations app."""

from django.contrib import admin

from .models import Quotation, QuotationItem


class QuotationItemInline(admin.TabularInline):
    model = QuotationItem
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


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    date_hierarchy = "quotation_date"
    list_display = [
        "display_number",
        "quotation_date",
        "party",
        "status",
        "grand_total",
        "valid_until",
    ]
    list_filter = ["status", "quotation_date"]
    search_fields = ["display_number", "party__name", "customer_name"]
    readonly_fields = [
        "business",
        "customer_name",
        "customer_gstin",
        "business_name",
        "business_gstin",
        "subtotal",
        "taxable_total",
        "cgst_total",
        "sgst_total",
        "igst_total",
        "grand_total",
        "converted_invoice",
        "converted_at",
        "created_at",
        "updated_at",
    ]
    inlines = [QuotationItemInline]
