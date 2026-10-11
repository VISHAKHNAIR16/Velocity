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
        # PROTECT, not CASCADE (2.0.10). With CASCADE, deleting ONE
        # BusinessProfile silently removed every party, item, invoice and
        # invoice line that belonged to it - legal and accounting records
        # destroyed by an admin click or a stray `User.objects.delete()`, with
        # nothing to stop it and nothing in the logs beyond the row count.
        #
        # "Close account" is deactivate + export, not delete. Every model that
        # inherits this now refuses to be deleted out from under its business.
        #
        # This changes no SQL: `on_delete` is enforced in Python by Django's
        # collector, not by the database, so the migration is metadata-only.
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True
