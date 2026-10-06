"""URL routes for the accounts app (mounted under /api/v1/)."""
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import BusinessLogoView, BusinessProfileView, RegisterView, StateListView


urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", TokenObtainPairView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("business/profile/", BusinessProfileView.as_view(), name="business-profile"),
    path("meta/states/", StateListView.as_view(), name="states"),
    path("business/logo/", BusinessLogoView.as_view(), name="business-logo"),
]