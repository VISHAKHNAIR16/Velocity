"""API views for authentication and the business profile."""

from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.constants import (
    GST_RATE_CHOICES,
    GST_STATE_CHOICES,
    MEASURING_UNITS,
    gst_rate_label,
)

from .models import BusinessProfile
from .serializers import BusinessProfileSerializer, LogoUploadSerializer, RegisterSerializer
from .services import get_business


class RegisterView(APIView):
    """POST /api/v1/auth/register/ : create account + business, return tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []  # ignore any stale token sent by the browser

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)  # log the user in straight away
        return Response(
            {"email": user.email, "access": str(refresh.access_token), "refresh": str(refresh)},
            status=201,
        )


class BusinessProfileView(RetrieveUpdateAPIView):
    """GET / PUT / PATCH /api/v1/business/profile/ : the caller's own profile only."""

    serializer_class = BusinessProfileSerializer

    def get_object(self) -> BusinessProfile:
        return get_business(self.request.user)


class BusinessLogoView(APIView):
    """PUT /api/v1/business/logo/ uploads or replaces the logo. DELETE removes it."""

    parser_classes = [MultiPartParser]  # file uploads arrive as multipart form data

    def put(self, request):
        profile = get_business(request.user)
        serializer = LogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_name = profile.logo.name
        profile.logo = serializer.validated_data["logo"]
        profile.save(update_fields=["logo", "updated_at"])

        # Remove the previous file only after the new one is safely stored.
        if old_name and old_name != profile.logo.name:
            profile.logo.storage.delete(old_name)

        return Response(BusinessProfileSerializer(profile, context={"request": request}).data)

    def delete(self, request):
        profile = get_business(request.user)
        if profile.logo:
            profile.logo.delete(save=True)  # deletes the stored file and clears the field
        return Response(status=status.HTTP_204_NO_CONTENT)


class StateListView(APIView):
    """GET /api/v1/meta/states/ : GST state codes for the frontend dropdown."""

    def get(self, request):
        return Response([{"code": code, "name": name} for code, name in GST_STATE_CHOICES])


class MeasuringUnitListView(APIView):
    """
    GET /api/v1/meta/units/ : the measuring units an invoice line may use.

    Served from the same constant the backend validates against, so a custom
    line in the browser can never offer a unit the server would reject.
    """

    def get(self, request):
        return Response([{"code": code, "name": name} for code, name in MEASURING_UNITS])


class GstRateListView(APIView):
    """
    GET /api/v1/meta/tax-rates/ : the GST slabs, with their display labels.

    The rates come from `GST_RATE_CHOICES`, which is also what the serializer
    validates line tax rates against, so the dropdown cannot drift from the
    server's idea of a valid rate.
    """

    def get(self, request):
        return Response(
            [
                {"code": str(rate), "name": gst_rate_label(rate)}
                for rate, _label in GST_RATE_CHOICES
            ]
        )
