"""URL routes for the dashboard app (mounted under /api/v1/)."""

from django.urls import path

from .views import DashboardLowStockView, DashboardStatsView

urlpatterns = [
    path("dashboard/stats/", DashboardStatsView.as_view(), name="dashboard-stats"),
    path("dashboard/low-stock/", DashboardLowStockView.as_view(), name="dashboard-low-stock"),
]
