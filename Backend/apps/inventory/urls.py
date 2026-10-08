"""URL routes for the inventory app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GstRateListView, ItemImageUploadView, ItemViewSet, UnitListView

router = DefaultRouter()
# NB: the router is already registered with the "items" prefix, so it is included
# at "" (not "items/"). Prefixing again would publish /api/v1/items/items/ and
# leave /api/v1/items/ resolving to the router's read-only API root.
router.register(r"items", ItemViewSet, basename="item")

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