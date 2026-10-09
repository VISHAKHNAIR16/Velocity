"""Views for the Parties app."""

from django.db.models import Q
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantModelViewSet

from .models import Party
from .serializers import PartyListSerializer, PartySerializer

#: Message used wherever the walk-in refuses an edit (decision 21). One constant
#: so the API, the tests and the frontend hint can never disagree.
WALK_IN_PROTECTED_MESSAGE = (
    "The walk-in / cash customer is managed by Velocity and cannot be renamed, "
    "given a GSTIN, or deleted. Create a new party for a real customer."
)


def _is_falsy(value) -> bool:
    """Treat JSON `false`, `"false"` and `"0"` all as False. A form may send any."""
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "off"}
    return False


def walk_in_protected_response():
    return Response(
        {
            "success": False,
            "error": "WALK_IN_PROTECTED",
            "message": WALK_IN_PROTECTED_MESSAGE,
            "errors": {},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


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
        # The walk-in is not deletable. `get_or_create_walk_in_party()` looks it
        # up by flag, so deactivating it would just resurrect an unusable row on
        # the next page load (or leave the counter sale with no party at all).
        if instance.is_walk_in:
            return walk_in_protected_response()
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    def destroy(self, request, *args, **kwargs):
        """Override so `perform_destroy` can refuse instead of silently no-op'ing."""
        instance = self.get_object()
        if instance.is_walk_in:
            return walk_in_protected_response()
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Refuse to deactivate the walk-in through PATCH/PUT (a soft delete)."""
        instance = self.get_object()
        if instance.is_walk_in and _is_falsy(request.data.get("is_active", True)):
            return walk_in_protected_response()
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Restore a soft-deleted party: POST /api/v1/parties/{id}/restore/"""
        party = self.get_object()
        if party.is_active:
            return Response(self.get_serializer(party).data)

        # The unique constraints only cover ACTIVE rows, so a GSTIN/PAN freed by
        # a soft delete may since have been taken by someone else. Detect that
        # here and explain it, instead of letting the database raise an
        # IntegrityError (which would surface as a 500).
        conflicts = []
        for field, label in (("gstin", "GSTIN"), ("pan", "PAN")):
            value = getattr(party, field)
            if not value:
                continue
            taken = Party.objects.filter(
                business=party.business, **{field: value}, is_active=True
            ).exclude(pk=party.pk)
            if taken.exists():
                conflicts.append(label)
        if conflicts:
            return Response(
                {
                    "success": False,
                    "error": "DUPLICATE_IDENTIFIER",
                    "message": (
                        f"Cannot restore: another active party in your business already "
                        f"uses this {' and '.join(conflicts)}."
                    ),
                    "errors": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        party.is_active = True
        party.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(party).data)

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