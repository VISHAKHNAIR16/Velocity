"""URL routes for the expenses app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExpenseCategoryViewSet, ExpenseViewSet

router = DefaultRouter()
router.register(r"expenses", ExpenseViewSet, basename="expense")
router.register(r"expense-categories", ExpenseCategoryViewSet, basename="expense-category")

urlpatterns = [
    path("", include(router.urls)),
]
