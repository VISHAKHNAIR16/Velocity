"""URL routes for the accounts app (mounted under /api/v1/)."""

from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    BusinessLogoView,
    BusinessProfileView,
    GstRateListView,
    MeasuringUnitListView,
    RegisterView,
    StateListView,
)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """
    Login with a tighter rate limit than the global default, so password
    guessing is slowed down. Note that DRF sees the proxy's shared IP unless
    NUM_PROXIES is configured, so this is a deterrent, not a hard guarantee
    (see FUTURE_CHECKLIST.md §1).
    """

    permission_classes = [AllowAny]
    throttle_scope = "login"


urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", ThrottledTokenObtainPairView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("business/profile/", BusinessProfileView.as_view(), name="business-profile"),
    path("meta/states/", StateListView.as_view(), name="states"),
    path("meta/units/", MeasuringUnitListView.as_view(), name="measuring-units"),
    path("meta/tax-rates/", GstRateListView.as_view(), name="gst-rates"),
    path("business/logo/", BusinessLogoView.as_view(), name="business-logo"),
]
