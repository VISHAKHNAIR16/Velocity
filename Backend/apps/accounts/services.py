"""Business-logic helpers for the accounts app."""

from .models import BusinessProfile

#: Name of the auto-created cash-sale customer. Retail sales are the majority
#: case for a shop counter, and `Invoice.party` is a required FK, so without a
#: walk-in customer every cash sale would force the user to create a party first.
WALK_IN_PARTY_NAME = "Walk-in / Cash Customer"

#: `Party.mobile` is required and validated as ^[6-9][0-9]{9}$. A walk-in has no
#: real phone number, so we use a fixed synthetic one. It is safe to share
#: because `Party` has no unique constraint on `mobile` (only on gstin/pan),
#: and this party is unique per business, not per sale.
WALK_IN_MOBILE = "9876543210"


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


def get_or_create_walk_in_party(business):
    """
    Return this business's walk-in customer, creating it on first use.

    **Looked up by the `is_walk_in` flag, not by name** (decision 21). Matching
    on a name breaks the moment a user renames the party, which is exactly the
    case the "protect it" rule refuses to allow - so name was never a safe key.

    The state is intentionally **left blank**. An over-the-counter sale is
    supplied from the business's own state, but that state is resolved at
    invoice time by `resolve_place_of_supply()` rather than stored here, so it
    can never go stale if the business later moves to a different state.

    Idempotent: safe to call on every page load, on registration, and from the
    billing form. Imported lazily to avoid a circular import
    (parties.models imports from accounts).
    """
    from apps.parties.models import Party

    # The `one_walk_in_per_business` partial unique constraint guarantees this
    # can match at most one row, so a plain filter is safe.
    existing = Party.objects.filter(business=business, is_walk_in=True).first()
    if existing is not None:
        return existing

    party = Party.objects.create(
        business=business,
        name=WALK_IN_PARTY_NAME,
        party_type=Party.PartyType.CUSTOMER,
        mobile=WALK_IN_MOBILE,
        email="",
        gstin="",
        pan="",
        is_walk_in=True,
        # Blank on purpose - resolved from the business at invoice time.
        state_code="",
    )
    return party