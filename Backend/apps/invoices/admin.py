"""
Admin for invoices.

An ISSUED or CANCELLED invoice is read-only here on purpose: the admin bypasses
the service layer, so allowing an edit would let someone change a legal document
without going through the snapshot / freeze rules in 1.4.4.
"""

from django.contrib import admin, messages

from .models import Invoice, InvoiceCounter, InvoiceItem


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    # Items are created/edited through the API so the snapshots and totals are
    # written by the service layer.
    fields = (
        "item_name", "item_type", "hsn_sac_code", "unit", "service_description",
        "quantity", "unit_price", "line_discount", "tax_rate",
        "invoice_discount_share", "taxable_value", "cgst_amount", "sgst_amount",
        "igst_amount", "total_amount",
    )
    readonly_fields = (
        "invoice_discount_share", "taxable_value", "cgst_amount", "sgst_amount",
        "igst_amount", "total_amount",
    )
    extra_readonly_fields = readonly_fields
    max_num = 0
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number", "party", "business", "invoice_date", "status",
        "grand_total", "supply_type",
    )
    list_filter = ("status", "supply_type", "invoice_date", "business")
    search_fields = ("invoice_number", "party__name", "recipient_name")
    date_hierarchy = "invoice_date"
    ordering = ("-invoice_date", "-id")
    inlines = [InvoiceItemInline]
    readonly_fields = (
        "invoice_number", "display_number", "status", "issued_at", "cancelled_at",
        "cancellation_reason", "created_by", "issued_by", "cancelled_by",
        "recipient_name", "recipient_gstin", "recipient_state_code",
        "recipient_address", "shipping_address",
        "business_name", "business_address", "business_gstin", "business_state_code",
        "document_title", "subtotal", "total_discount", "taxable_total",
        "cgst_total", "sgst_total", "igst_total", "round_off", "grand_total",
        "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        # Drafts are created through the API so the calculator always runs.
        return False

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj is not None and obj.status != Invoice.Status.DRAFT:
            # Frozen: nothing may be changed once issued or cancelled.
            return base + ["party", "invoice_date", "due_date", "place_of_supply",
                           "prices_include_tax", "notes", "terms"]
        return base

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status != Invoice.Status.DRAFT:
            self.message_user(
                request,
                f"Invoice {obj.display_number} is {obj.get_status_display().lower()} "
                "and is read-only.",
                level=messages.WARNING,
            )
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status != Invoice.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(InvoiceCounter)
class InvoiceCounterAdmin(admin.ModelAdmin):
    list_display = ("business", "financial_year", "number_prefix", "last_number")
    list_filter = ("financial_year",)
    readonly_fields = ("last_number",)

    def has_add_permission(self, request):
        return False