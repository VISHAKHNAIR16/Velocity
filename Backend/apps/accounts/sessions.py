"""
Multiple-login support: recording, listing and revoking sessions.

Why this module exists
----------------------
The project shipped with a refresh token that lived for 7 days and could not be
invalidated. Consequences:

* "Log out" only deleted the token from the browser. The token itself stayed
  valid, so a stolen phone or shared laptop kept access for up to a week.
* A user had no way to see they were signed in on three devices.
* There was no way to cut off one device without changing the password, which
  logs you out everywhere and is a blunt instrument for a shop owner.

Everything here is built on SimpleJWT's blacklist: revoking a session blacklists
that session's refresh token `jti`, so it can never be exchanged for a new access
token again.

**Access tokens are not revocable.** They are stateless and self-contained, so
one already in the wild stays valid until it expires (30 minutes). Revoking a
refresh token bounds the damage to that lifetime rather than eliminating it. This
is inherent to stateless JWTs, not a shortcut here - so a session is described as
"revoked" honestly and the UI should not promise an instant cut-off.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from .models import UserSession

#: Browser names we bother to identify. Anything unrecognised falls back to the
#: raw user-agent prefix, so the list never shows an empty "unknown device" for
#: something we can describe.
_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Chrome/", "Chrome"),
    ("Firefox/", "Firefox"),
    ("Safari/", "Safari"),
)

_PLATFORMS = (
    # Mobile tokens are checked FIRST on purpose: an iPhone/iPad User-Agent
    # contains the literal string "like Mac OS X", so a macOS check placed above
    # them would label every iPhone as "macOS".
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


def describe_device(user_agent: str) -> str:
    """Turn a User-Agent header into "Chrome on Windows".

    Display only. Never used for an authorisation decision - a User-Agent is
    trivially spoofed, so trusting it would be a security bug.
    """
    if not user_agent:
        return "Unknown device"
    browser = next((name for token, name in _BROWSERS if token in user_agent), "Browser")
    platform = next((name for token, name in _PLATFORMS if token in user_agent), "")
    return f"{browser} on {platform}" if platform else browser


def client_ip(request) -> str | None:
    """Best-effort client IP.

    Deliberately reads REMOTE_ADDR, NOT X-Forwarded-For. Behind Render the
    forwarded header is client-controlled unless NUM_PROXIES is configured
    correctly (FUTURE_CHECKLIST A1 / 2.0.5), and this value is shown to the user
    as "where you signed in" - so a spoofable value would be misleading. The
    proxy's own address is an acceptable answer until 2.0.5 lands.
    """
    return request.META.get("REMOTE_ADDR") or None


def record_login(request, user, refresh_token: RefreshToken) -> UserSession:
    """Create the session row for a successful login.

    Called after tokens are issued. Idempotent per `jti`, so a retried login
    cannot produce two rows for one token.
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:400]
    session, _created = UserSession.objects.update_or_create(
        jti=refresh_token["jti"],
        defaults={
            "user": user,
            "device": describe_device(user_agent),
            "ip_address": client_ip(request),
            "user_agent": user_agent,
            "revoked_at": None,
        },
    )
    return session


def attach_session(user, refresh_token: RefreshToken, request) -> UserSession:
    """Carried forward by the rotated token, so 'this device' stays stable.

    `BLACKLIST_AFTER_ROTATION` retires the old refresh token on every refresh. If
    the new token did not inherit the session row, the device would vanish from
    the list and reappear as a new login on the next refresh - and "revoke this
    device" would never converge.
    """
    jti = refresh_token["jti"]
    # UPDATE rather than update_or_create: an update_or_create on `jti` alone
    # would try to CREATE a row with no user on the miss path, which is a NOT NULL
    # violation. A miss means the token predates this feature, so record it.
    if UserSession.objects.filter(jti=jti).update(last_used_at=timezone.now()):
        return UserSession.objects.get(jti=jti)
    return record_login(request, user, refresh_token)


def _blacklist(jti: str) -> bool:
    """Blacklist one refresh token by `jti`. Idempotent."""
    try:
        token = OutstandingToken.objects.get(jti=jti)
    except OutstandingToken.DoesNotExist:
        return False
    BlacklistedToken.objects.get_or_create(token=token)
    return True


def revoke_session(session: UserSession) -> UserSession:
    """Sign out ONE device. Other devices keep working."""
    if session.revoked_at is None:
        _blacklist(session.jti)
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])
    return session


def revoke_all_except(user, keep_session_id: int | None = None) -> int:
    """'Sign out everywhere else'. Returns how many sessions were revoked."""
    queryset = UserSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep_session_id is not None:
        queryset = queryset.exclude(pk=keep_session_id)
    revoked = 0
    for session in queryset:
        revoke_session(session)
        revoked += 1
    return revoked


def active_sessions(user) -> list[UserSession]:
    """Every session the user may still use, newest activity first."""
    return list(UserSession.objects.filter(user=user, revoked_at__isnull=True))