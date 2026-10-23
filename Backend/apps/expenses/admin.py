"""Admin for the expenses app."""

from django.contrib import admin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "is_default", "business"]
    list_filter = ["is_default"]
    search_fields = ["name"]
    readonly_fields = ["business", "created_at", "updated_at"]


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    date_hierarchy = "expense_date"
    list_display = ["expense_date", "category", "amount", "tax_paid", "payment_mode"]
    list_filter = ["payment_mode", "expense_date", "category"]
    search_fields = ["category__name", "reference_number", "notes"]
    readonly_fields = ["business", "created_at", "updated_at"]
