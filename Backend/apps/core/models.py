"""Abstract base for every model that belongs to one business (tenant)."""

from django.db import models


class TenantQuerySet(models.QuerySet):
    def for_business(self, business) -> "TenantQuerySet":
        """Only this business's rows. Use this in services and reports."""
        return self.filter(business=business)


class TenantModel(models.Model):
    """
    Inherit from this for Party, Item, Invoice, Purchase, Expense, ...
    It adds the owning business plus created/updated timestamps.

    Rule: never put `business` in a serializer's writable fields. The view
    sets it from the logged-in user.
    """

    business = models.ForeignKey(
        "accounts.BusinessProfile",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True
