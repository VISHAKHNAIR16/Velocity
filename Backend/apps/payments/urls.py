"""URL routes for the payments app (mounted under /api/v1/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PaymentRecordViewSet

router = DefaultRouter()
router.register(r"payments", PaymentRecordViewSet, basename="payment")

urlpatterns = [
    path("", include(router.urls)),
]
