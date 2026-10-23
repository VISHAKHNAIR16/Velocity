"""
Expense API tests (4.1).

Covers: create expense, edit/delete (expenses are editable unlike payments),
summary aggregation, category CRUD, tenant isolation.
"""

from django.test import TestCase
from rest_framework import status

from .base import (
    EXPENSE_LIST_URL,
    api_client_for,
    make_expense,
    make_expense_category,
    make_user,
)


class ExpenseCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.category = make_expense_category(self.user, "Rent")

    def test_create_expense(self):
        payload = {
            "category": self.category.pk,
            "expense_date": "2026-02-01",
            "amount": "5000.00",
            "tax_paid": "900.00",
            "payment_mode": "BANK_TRANSFER",
            "reference_number": "NEFT123",
            "notes": "Office rent Feb",
        }
        response = self.client_.post(EXPENSE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["amount"], "5000.00")
        self.assertEqual(data["tax_paid"], "900.00")
        self.assertEqual(data["total_with_tax"], "5900.00")

    def test_rejects_zero_amount(self):
        payload = {
            "category": self.category.pk,
            "expense_date": "2026-02-01",
            "amount": "0.00",
            "payment_mode": "CASH",
        }
        response = self.client_.post(EXPENSE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_edit_expense(self):
        expense = make_expense(self.user, self.category, amount="100.00")
        response = self.client_.patch(
            f"/api/v1/expenses/{expense.pk}/",
            {"amount": "200.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.json()["amount"], "200.00")

    def test_delete_expense(self):
        expense = make_expense(self.user, self.category, amount="100.00")
        response = self.client_.delete(f"/api/v1/expenses/{expense.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.content)

    def test_summary_by_category(self):
        make_expense(self.user, self.category, amount="1000.00", tax_paid="100.00")
        cat2 = make_expense_category(self.user, "Utilities")
        make_expense(self.user, cat2, amount="500.00", tax_paid="50.00")

        response = self.client_.get("/api/v1/expenses/summary/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()
        self.assertEqual(data["grand_total"], "1500.00")
        self.assertEqual(len(data["by_category"]), 2)

    def test_list_tenant_scoped(self):
        make_expense(self.user, self.category, amount="100.00")

        other = make_user(email="other@example.com", state_code="29")
        other_cat = make_expense_category(other, "Other Rent")
        make_expense(other, other_cat, amount="999.00")

        response = self.client_.get(EXPENSE_LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
