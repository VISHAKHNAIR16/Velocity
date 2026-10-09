"""API views for authentication, sessions and the business profile."""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.constants import (
    GST_RATE_CHOICES,
    GST_STATE_CHOICES,
    MEASURING_UNITS,
    gst_rate_label,
)

from .models import BusinessProfile, UserSession
from .serializers import BusinessProfileSerializer, LogoUploadSerializer, RegisterSerializer
from .services import get_business
from .sessions import (
    active_sessions,
    attach_session,
    record_login,
    revoke_all_except,
    revoke_session,
)

User = get_user_model()


class RegisterView(APIView):
    """POST /api/v1/auth/register/ : create account + business, return tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []  # ignore any stale token sent by the browser

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)  # log the user in straight away
        # Registration is a login, so it gets a session row too - otherwise this
        # device would be invisible in "signed-in devices" until the next login.
        record_login(request, user, refresh)
        return Response(
            {"email": user.email, "access": str(refresh.access_token), "refresh": str(refresh)},
            status=201,
        )


class SessionLoginView(TokenObtainPairView):
    """
    Login that records WHICH device signed in.

    Plain `TokenObtainPairView` hands out a refresh token with no record of where
    it went, so the user could never see or revoke a device.
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        refresh_raw = response.data.get("refresh") if response.status_code == 200 else None
        if refresh_raw:
            try:
                refresh = RefreshToken(refresh_raw)
                # `request.user` is AnonymousUser here: this view validates a
                # password but never authenticates the request, so the user has
                # to come from the token we just issued.
                user = User.objects.get(pk=refresh["user_id"])
                record_login(request, user, refresh)
            except (TokenError, User.DoesNotExist):
                # Never fail a successful login over bookkeeping.
                pass
        return response


class SessionTokenRefreshView(TokenRefreshView):
    """Refresh that carries the session row onto the rotated token.

    Without this the device would disappear from the session list every time the
    token rotated, and 'revoke this device' would never take effect.
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        refresh_raw = response.data.get("refresh") if response.status_code == 200 else None
        if refresh_raw:
            try:
                refresh = RefreshToken(refresh_raw)
                # Same reason as the login view: this endpoint is AllowAny, so
                # `request.user` is AnonymousUser. The user comes from the token.
                user = User.objects.get(pk=refresh["user_id"])
                attach_session(user, refresh, request)
            except (TokenError, User.DoesNotExist):
                pass
        return response


class SessionListView(APIView):
    """
    GET  /api/v1/auth/sessions/  - every device signed in to this account
    DELETE /api/v1/auth/sessions/{id}/ - sign out ONE device
    """

    def get(self, request):
        sessions = active_sessions(request.user)
        data = [
            {
                "id": s.pk,
                "device": s.device,
                "ip_address": s.ip_address,
                "created_at": s.created_at,
                "last_used_at": s.last_used_at,
            }
            for s in sessions
        ]
        return Response({"success": True, "count": len(data), "sessions": data})

    def delete(self, request, pk=None):
        try:
            session = UserSession.objects.get(pk=pk, user=request.user)
        except UserSession.DoesNotExist:
            return Response(
                {"success": False, "error": "SESSION_NOT_FOUND", "message": "No such session.", "errors": {}},
                status=status.HTTP_404_NOT_FOUND,
            )
        revoke_session(session)
        return Response({"success": True, "revoked": session.pk})


class RevokeOtherSessionsView(APIView):
    """POST /api/v1/auth/sessions/revoke-others/ - 'sign out everywhere else'.

    The caller's own device stays signed in, which is what the button says.
    """

    def post(self, request):
        keep = None
        raw = request.data.get("refresh") or request.query_params.get("refresh")
        if raw:
            try:
                current = UserSession.objects.filter(
                    jti=RefreshToken(raw)["jti"], user=request.user
                ).first()
                keep = current.pk if current else None
            except TokenError:
                keep = None
        revoked = revoke_all_except(request.user, keep_session_id=keep)
        return Response({"success": True, "revoked": revoked})


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout/ - invalidate THIS device's refresh token.

    The client deletes its own copy too, but that is not what makes logout real:
    blacklisting the refresh token here is. Without it, "log out" only removed
    the token from the browser and the token stayed usable for its full 7 days.

    `AllowAny` **by design**: the refresh token is the credential this endpoint
    consumes, so demanding a valid access token as well would make logout
    impossible at exactly the moment it matters most - when the access token has
    expired. A caller with no refresh token gets a clear 400 and changes nothing.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.data.get("refresh") or request.query_params.get("refresh")
        if not raw:
            return Response(
                {
                    "success": False,
                    "error": "REFRESH_TOKEN_REQUIRED",
                    "message": "Send the refresh token to log out.",
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(raw)
        except TokenError:
            # Already expired or already blacklisted: nothing left to invalidate,
            # so report success rather than an error the user cannot act on.
            return Response({"success": True, "already_gone": True})

        # The user comes from the token, not from `request.user`: this view is
        # AllowAny, so an unauthenticated request leaves that as AnonymousUser
        # and filtering on it raises TypeError.
        owner = User.objects.filter(pk=token["user_id"]).first()
        session = (
            UserSession.objects.filter(jti=token["jti"], user=owner).first()
            if owner
            else None
        )
        if session is not None:
            revoke_session(session)
        else:
            token.blacklist()
        return Response({"success": True})


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
