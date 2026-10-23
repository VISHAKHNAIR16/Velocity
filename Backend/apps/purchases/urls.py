"""URL routes for the purchases app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PurchaseReturnViewSet, PurchaseViewSet

router = DefaultRouter()
router.register(r"purchases", PurchaseViewSet, basename="purchase")
router.register(r"purchases/returns", PurchaseReturnViewSet, basename="purchase-return")

urlpatterns = [
    path("", include(router.urls)),
]
