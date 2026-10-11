"""Django admin for purchases.

Registered for convenience only. The admin deliberately does **not** offer a
create or edit form for a posted bill: money on a filed document must not be
editable from the database browser, which bypasses every serializer check in
the codebase.
"""

from django.contrib import admin

from apps.purchases.models import Purchase, PurchaseItem, PurchaseStatus


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    fields = (
        "item",
        "item_name",
        "item_type",
        "quantity",
        "unit_price",
        "tax_rate",
        "taxable_value",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "total_amount",
        "itc_eligible",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Read-only, always. Editing a line of a filed bill would silently
        # disagree with the header, and the audit command would then report a
        # mismatch with nothing that explains how it happened.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "bill_number",
        "supplier_name",
        "bill_date",
        "status",
        "grand_total",
        "has_divergence",
    )
    list_filter = ("status", "financial_year", "has_divergence")
    search_fields = ("bill_number", "supplier_name")
    inlines = [PurchaseItemInline]
    readonly_fields = [field.name for field in Purchase._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # A purchase is a filed document. It is cancelled, never deleted.
        return False

    @admin.action(description="Mark selected bills as cancelled")
    def cancel_selected(self, request, queryset):
        for purchase in queryset.filter(status=PurchaseStatus.POSTED):
            purchase.status = PurchaseStatus.CANCELLED
            purchase.save(update_fields=["status"])
