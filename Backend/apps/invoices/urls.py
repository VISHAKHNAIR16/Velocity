"""URL routes for the invoices app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GSTR1View, GSTR3BView, InvoiceViewSet

router = DefaultRouter()
# Already registered with the "invoices" prefix, so it is included at "".
# Prefixing again would publish /api/v1/invoices/invoices/ and leave
# /api/v1/invoices/ resolving to the router's read-only API root.
router.register(r"invoices", InvoiceViewSet, basename="invoice")

urlpatterns = [
    path("", include(router.urls)),
    path("gstr1/", GSTR1View.as_view(), name="gstr1"),
    path("gstr3b/", GSTR3BView.as_view(), name="gstr3b"),
]
