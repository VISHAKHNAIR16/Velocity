"""Views for the Parties app."""

from django.db.models import Q
from rest_framework import filters
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantModelViewSet

from .models import Party
from .serializers import PartyListSerializer, PartySerializer


class PartyViewSet(TenantModelViewSet):
    """
    CRUD for Parties (Customers & Vendors) scoped to the logged-in user's business.

    Endpoints:
    - GET    /api/v1/parties/           List (with filters, search, pagination)
    - POST   /api/v1/parties/           Create
    - GET    /api/v1/parties/{id}/      Retrieve
    - PUT    /api/v1/parties/{id}/      Full update
    - PATCH  /api/v1/parties/{id}/      Partial update
    - DELETE /api/v1/parties/{id}/      Soft delete (sets is_active=False)
    """

    queryset = Party.objects.all()
    serializer_class = PartySerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "mobile", "email", "gstin", "pan"]
    ordering_fields = ["name", "party_type", "created_at", "state_code"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return PartyListSerializer
        return PartySerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by party_type (CUSTOMER, SUPPLIER, BOTH)
        # BOTH parties should appear in both CUSTOMER and SUPPLIER filters
        party_type = self.request.query_params.get("party_type")
        if party_type:
            pt = party_type.upper()
            if pt == Party.PartyType.CUSTOMER:
                queryset = queryset.filter(
                    party_type__in=[Party.PartyType.CUSTOMER, Party.PartyType.BOTH]
                )
            elif pt == Party.PartyType.SUPPLIER:
                queryset = queryset.filter(
                    party_type__in=[Party.PartyType.SUPPLIER, Party.PartyType.BOTH]
                )
            else:
                queryset = queryset.filter(party_type=pt)

        # Filter by is_active (default true for list, but allow override)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")
        elif self.action == "list":
            # By default, only show active parties in list
            queryset = queryset.filter(is_active=True)

        return queryset

    def perform_destroy(self, instance: Party):
        """Soft delete: set is_active=False instead of hard delete."""
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Restore a soft-deleted party: POST /api/v1/parties/{id}/restore/"""
        party = self.get_object()
        party.is_active = True
        party.save(update_fields=["is_active", "updated_at"])
        serializer = self.get_serializer(party)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def search(self, request):
        """
        Lightweight search for autocomplete dropdowns.
        GET /api/v1/parties/search/?q=term&party_type=CUSTOMER
        Returns: [{id, name, gstin, state_code, party_type}, ...]
        """
        q = request.query_params.get("q", "").strip()
        party_type = request.query_params.get("party_type")

        queryset = self.get_queryset().filter(is_active=True)

        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(mobile__icontains=q) | Q(gstin__icontains=q)
            )

        if party_type:
            pt = party_type.upper()
            if pt == Party.PartyType.CUSTOMER:
                queryset = queryset.filter(
                    party_type__in=[Party.PartyType.CUSTOMER, Party.PartyType.BOTH]
                )
            elif pt == Party.PartyType.SUPPLIER:
                queryset = queryset.filter(
                    party_type__in=[Party.PartyType.SUPPLIER, Party.PartyType.BOTH]
                )
            else:
                queryset = queryset.filter(party_type=pt)

        queryset = queryset[:20]  # Limit for autocomplete

        data = [
            {
                "id": p.id,
                "name": p.name,
                "gstin": p.gstin,
                "state_code": p.state_code,
                "party_type": p.party_type,
                "display_state": p.get_display_state(),
            }
            for p in queryset
        ]
        return Response(data)