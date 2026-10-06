"""Business-logic helpers for the accounts app."""

from .models import BusinessProfile


def get_business(user) -> BusinessProfile:
    """
    Return the business profile of `user`. This is the ONE place the project
    answers "which business does this user belong to?".

    Profiles are normally created at registration. Accounts created another way
    (e.g. `createsuperuser`) get one on first use, so callers never need to handle
    a missing profile.
    """
    try:
        return user.business  # cached on the user object after the first access
    except BusinessProfile.DoesNotExist:
        profile, _ = BusinessProfile.objects.get_or_create(
            user=user,
            defaults={"trade_name": user.email, "company_name": user.email, "email": user.email},
        )
        return profile
