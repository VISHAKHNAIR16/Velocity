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

    The state code is the business's own state, so an over-the-counter sale is
    treated as intra-state (CGST + SGST) unless the business overrides it - which
    matches how counter sales actually work.

    Idempotent: safe to call on every page load, on registration, and from the
    billing form. Imported lazily to avoid a circular import
    (parties.models imports from accounts).
    """
    from apps.parties.models import Party

    party, _created = Party.objects.get_or_create(
        business=business,
        name=WALK_IN_PARTY_NAME,
        defaults={
            "party_type": Party.PartyType.CUSTOMER,
            "mobile": WALK_IN_MOBILE,
            "email": "",
            "gstin": "",
            "pan": "",
            # Falls back to the billing state on an invoice. An unregistered
            # walk-in must not carry a GSTIN.
            "state_code": business.state_code or "",
        },
    )
    # Keep the walk-in usable: if a previous run created it while the business
    # had no state, fill the state in now that one is set.
    if not party.state_code and business.state_code:
        party.state_code = business.state_code
        party.save(update_fields=["state_code", "updated_at"])
    return party