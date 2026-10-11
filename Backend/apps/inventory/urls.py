"""URL routes for the inventory app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    GstRateListView,
    ItemImageUploadView,
    ItemViewSet,
    StockMovementViewSet,
    UnitListView,
)

router = DefaultRouter()
# NB: the router is already registered with the "items" prefix, so it is included
# at "" (not "items/"). Prefixing again would publish /api/v1/items/items/ and
# leave /api/v1/items/ resolving to the router's read-only API root.
router.register(r"items", ItemViewSet, basename="item")
# The ledger is a first-class thing in its own right, not an Item sub-resource:
# it has its own lifecycle (append-only) and is read across items. Mounted at
# `stock/movements` to match tasks.md 2.1.4, so the documented URL is the real
# URL rather than a shape someone has to remember.
router.register(r"stock/movements", StockMovementViewSet, basename="stockmovement")

urlpatterns = [
    path("", include(router.urls)),
    # Image upload lives outside the router so it keeps a distinct parser and
    # stays readable as a plain file endpoint.
    path(
        "items/<int:pk>/image/",
        ItemImageUploadView.as_view(),
        name="item-image",
    ),
    # Form dropdown sources, kept next to the models that validate them.
    path("meta/units/", UnitListView.as_view(), name="meta-units"),
    path("meta/gst-rates/", GstRateListView.as_view(), name="meta-gst-rates"),
]