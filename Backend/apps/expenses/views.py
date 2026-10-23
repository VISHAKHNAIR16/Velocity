"""
Views for expenses.

Endpoints:
- GET    /api/v1/expenses/           List (scoped to business, filterable)
- POST   /api/v1/expenses/           Create an expense
- GET    /api/v1/expenses/{id}/      Retrieve
- PUT/PATCH /api/v1/expenses/{id}/   Edit
- DELETE /api/v1/expenses/{id}/      Delete
- GET    /api/v1/expenses/summary/   Aggregation by category and month
- GET    /api/v1/expenses/categories/ List expense categories
"""

from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.services import get_business
from apps.core.money import q2
from apps.core.viewsets import TenantModelViewSet

from .api_errors import translate
from .errors import ExpenseError
from .models import Expense, ExpenseCategory
from .serializers import ExpenseCategorySerializer, ExpenseSerializer


class ExpenseViewSet(TenantModelViewSet):
    """CRUD for expenses, scoped to the logged-in business."""

    queryset = Expense.objects.select_related("category", "business")
    serializer_class = ExpenseSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["category__name", "reference_number", "notes"]
    ordering_fields = ["expense_date", "amount", "created_at"]
    ordering = ["-expense_date", "-id"]

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)
        payment_mode = self.request.query_params.get("payment_mode")
        if payment_mode:
            queryset = queryset.filter(payment_mode=payment_mode.upper())
        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(expense_date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(expense_date__lte=date_to)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save(business=get_business(request.user))
        except ExpenseError as exc:
            raise translate(exc) from exc
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """GET /api/v1/expenses/summary/ - totals by category and month."""
        queryset = self.get_queryset()
        by_category = (
            queryset.values("category__name")
            .annotate(total=Sum("amount"), tax=Sum("tax_paid"))
            .order_by("-total")
        )
        by_month = (
            queryset.annotate(month=TruncMonth("expense_date"))
            .values("month")
            .annotate(total=Sum("amount"), tax=Sum("tax_paid"))
            .order_by("-month")
        )
        grand_total = queryset.aggregate(total=Sum("amount"), tax=Sum("tax_paid"))
        return Response(
            {
                "by_category": list(by_category),
                "by_month": list(by_month),
                "grand_total": str(q2(grand_total["total"] or Decimal("0"))),
                "grand_tax": str(q2(grand_total["tax"] or Decimal("0"))),
            }
        )


class ExpenseCategoryViewSet(TenantModelViewSet):
    """CRUD for expense categories."""

    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer

    def get_queryset(self):
        return super().get_queryset().filter(is_default=True) | super().get_queryset().filter(
            business=self.request.user.business
        )

    def perform_create(self, serializer):
        serializer.save(business=get_business(self.request.user), is_default=False)
