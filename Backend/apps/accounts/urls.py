"""URL routes for the accounts app (mounted under /api/v1/)."""

from django.urls import path
from rest_framework.permissions import AllowAny

from .views import (
    BusinessLogoView,
    BusinessProfileView,
    GstRateListView,
    LogoutView,
    MeasuringUnitListView,
    RegisterView,
    RevokeOtherSessionsView,
    SessionListView,
    SessionLoginView,
    SessionTokenRefreshView,
    StateListView,
)

#: Where an unauthenticated caller may reach, and at what rate.
#:
#: `auth` (10/minute) is deliberately stricter than the global `anon` 60/hour.
#: Login, registration and token refresh are the three endpoints worth guessing
#: at: passwords, account creation, and token churn. Before 2.0.5 `login` and
#: `preview` had scopes but **register and refresh had none**, so both fell
#: through to the anonymous bucket - the two least-protected endpoints in the
#: API (1.4.9 finding #3).
AUTH_SCOPE = "auth"


class ThrottledTokenObtainPairView(SessionLoginView):
    """
    Login with a tighter rate limit than the global default, so password
    guessing is slowed down.

    **NOTE (1.4.9 finding #14):** `NUM_PROXIES` is *not* configured, so behind
    Render's proxy DRF sees the proxy's shared IP and every anonymous caller
    lands in one bucket. An earlier docstring here claimed it was configured,
    which was wrong - this is the comment a future reader would have trusted.
    `FUTURE_CHECKLIST.md` A1 tracks the fix (2.0.5). Until then this is a
    deterrent against one attacker, not a hard guarantee.
    """

    permission_classes = [AllowAny]
    throttle_scope = AUTH_SCOPE


class ThrottledRegisterView(RegisterView):
    """Registration is unauthenticated, so it needs the auth throttle too."""

    permission_classes = [AllowAny]
    throttle_scope = AUTH_SCOPE


class ThrottledTokenRefreshView(SessionTokenRefreshView):
    """Token refresh is unauthenticated (it carries the refresh token, not a session)."""

    permission_classes = [AllowAny]
    throttle_scope = AUTH_SCOPE


urlpatterns = [
    path("auth/register/", ThrottledRegisterView.as_view(), name="register"),
    path("auth/login/", ThrottledTokenObtainPairView.as_view(), name="login"),
    path("auth/refresh/", ThrottledTokenRefreshView.as_view(), name="token-refresh"),
    # --- Sessions (multiple logins) ---
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/sessions/", SessionListView.as_view(), name="sessions"),
    path("auth/sessions/revoke-others/", RevokeOtherSessionsView.as_view(), name="sessions-revoke-others"),
    path("auth/sessions/<int:pk>/", SessionListView.as_view(), name="session-detail"),
    path("business/profile/", BusinessProfileView.as_view(), name="business-profile"),
    path("meta/states/", StateListView.as_view(), name="states"),
    path("meta/units/", MeasuringUnitListView.as_view(), name="measuring-units"),
    path("meta/tax-rates/", GstRateListView.as_view(), name="gst-rates"),
    path("business/logo/", BusinessLogoView.as_view(), name="business-logo"),
]