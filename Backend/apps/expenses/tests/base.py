"""
Shared fixtures for the expenses API tests.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import BusinessProfile
from apps.expenses.models import Expense, ExpenseCategory

User = get_user_model()

EXPENSE_LIST_URL = "/api/v1/expenses/"
EXPENSE_CATEGORY_URL = "/api/v1/expense-categories/"


def make_user(email="owner@example.com", *, state_code="36"):
    user = User.objects.create_user(email=email, password="Str0ngPass!234", first_name="Owner")
    BusinessProfile.objects.create(
        user=user,
        trade_name=f"{email} Traders",
        company_name=f"{email} Traders",
        owner_name="Owner",
        phone="9000000000",
        state_code=state_code,
        gstin="36AAAAA0000A1Z5",
        pan="AAAAA0000A",
        address_line="1 Test Street",
        city="Hyderabad",
        pincode="500001",
    )
    return user


def make_expense_category(user, name="Rent", *, is_default=False):
    return ExpenseCategory.objects.create(
        business=user.business,
        name=name,
        is_default=is_default,
    )


def make_expense(user, category, *, amount="100.00", tax_paid="0.00", expense_date="2026-02-01"):
    return Expense.objects.create(
        business=user.business,
        category=category,
        expense_date=expense_date,
        amount=Decimal(amount),
        tax_paid=Decimal(tax_paid),
        payment_mode=Expense.PaymentMode.CASH,
    )


def api_client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client
