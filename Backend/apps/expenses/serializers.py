"""
Serializers for expenses.
"""

from rest_framework import serializers

from apps.core.fields import TenantPrimaryKeyRelatedField

from .models import Expense, ExpenseCategory

ZERO = "0.00"


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ["id", "name", "is_default", "description", "created_at", "updated_at"]
        read_only_fields = ["id", "is_default", "created_at", "updated_at"]


class ExpenseSerializer(serializers.ModelSerializer):
    """Full expense record."""

    category = TenantPrimaryKeyRelatedField(queryset=ExpenseCategory.objects.all())
    category_name = serializers.CharField(source="category.name", read_only=True)
    total_with_tax = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "category",
            "category_name",
            "expense_date",
            "amount",
            "tax_paid",
            "total_with_tax",
            "payment_mode",
            "reference_number",
            "notes",
            "receipt_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "category_name",
            "total_with_tax",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "reference_number": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
            "receipt_url": {"required": False, "allow_blank": True},
            "tax_paid": {"required": False, "default": ZERO},
        }

    def validate_amount(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value
