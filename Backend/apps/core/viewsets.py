"""Reusable DRF building blocks that enforce multi-tenant data isolation."""

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from rest_framework import viewsets

from apps.accounts.services import get_business


class TenantQuerysetMixin:
    """
    Scopes a ViewSet to the logged-in user's business:
      - get_queryset(): only that business's rows (so other tenants' IDs give 404)
      - perform_create(): new rows are stamped with that business
    """

    def get_business(self):
        return get_business(self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        try:
            queryset.model._meta.get_field("business")
        except FieldDoesNotExist as exc:
            raise ImproperlyConfigured(
                f"{queryset.model.__name__} must inherit from TenantModel to be used "
                "in a tenant-scoped view."
            ) from exc
        return queryset.filter(business=self.get_business())

    def perform_create(self, serializer):
        serializer.save(business=self.get_business())


class TenantModelViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    """Full CRUD, scoped to the caller's business. Set `queryset` and `serializer_class`."""


class TenantReadOnlyViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """List and retrieve only, scoped to the caller's business."""
