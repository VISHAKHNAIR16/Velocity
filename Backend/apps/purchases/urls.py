"""URL routes for the purchases app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PurchaseViewSet

router = DefaultRouter()
# Included at "" because the router already carries the "purchases" prefix.
# Prefixing again would publish /api/v1/purchases/purchases/.
router.register(r"purchases", PurchaseViewSet, basename="purchase")

urlpatterns = [
    path("", include(router.urls)),
]
