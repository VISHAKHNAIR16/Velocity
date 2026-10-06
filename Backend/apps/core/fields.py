"""Serializer fields that respect tenant boundaries."""

from rest_framework import serializers

from apps.accounts.services import get_business


class TenantPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """
    A foreign-key field that only accepts objects owned by the caller's business.
    Use it for EVERY relation to another tenant model (invoice -> party, line -> item).

        party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return queryset.none()  # fail closed
        return queryset.filter(business=get_business(request.user))
