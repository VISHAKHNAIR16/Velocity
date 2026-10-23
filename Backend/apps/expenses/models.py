"""
Expense models for the Velocity GST Billing platform.

Expense categories seed the most common SME overheads (Rent, Utilities,
Salaries, Transport, Office Supplies). Users can add custom categories.

Design rules:
1. Expenses are editable (unlike payments which are append-only).
2. Receipt attachment is a Cloudinary URL (consistent with 2.3 logo pattern).
3. Tenancy enforced by TenantModel + TenantPrimaryKeyRelatedField.
"""

from decimal import Decimal

from django.db import models

from apps.core.models import TenantModel

ZERO = Decimal("0.00")


class ExpenseCategory(TenantModel):
    """A category of operational expense."""

    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"],
                name="unique_expense_category_per_business",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Expense(TenantModel):
    """One operational expense incurred by the business."""

    class PaymentMode(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        UPI = "UPI", "UPI"
        CHEQUE = "CHEQUE", "Cheque"

    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    expense_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tax_paid = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    reference_number = models.CharField(max_length=100, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    receipt_url = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [
            models.Index(fields=["business", "expense_date"]),
            models.Index(fields=["business", "category"]),
            models.Index(fields=["business", "payment_mode"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expense_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(tax_paid__gte=0), name="expense_tax_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category.name} - {self.amount} on {self.expense_date}"

    @property
    def total_with_tax(self) -> Decimal:
        return self.amount + self.tax_paid
