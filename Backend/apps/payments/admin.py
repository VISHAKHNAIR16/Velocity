"""Admin for the payments app.

Payments are append-only accounting records: they can be viewed but not
created, edited or deleted through the admin.
"""

from django.contrib import admin

from .models import PaymentRecord


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    date_hierarchy = "payment_date"
    list_display = [
        "payment_date",
        "party",
        "get_transaction_type_display",
        "amount",
        "payment_mode",
        "reference_number",
        "invoice",
    ]
    list_filter = ["transaction_type", "payment_mode", "payment_date"]
    search_fields = ["party__name", "reference_number"]
    readonly_fields = [
        "business",
        "party",
        "transaction_type",
        "invoice",
        "payment_date",
        "payment_mode",
        "amount",
        "reference_number",
        "notes",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
