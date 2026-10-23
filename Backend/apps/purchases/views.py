"""
Views for purchases.

Endpoints:
- GET    /api/v1/purchases/           List (scoped to business, filterable)
- POST   /api/v1/purchases/           Create a purchase bill
- GET    /api/v1/purchases/{id}/      Retrieve
- PATCH  /api/v1/purchases/{id}/      Edit - DRAFT only
- DELETE /api/v1/purchases/{id}/      Delete - DRAFT only
- POST   /api/v1/purchases/{id}/issue/  Issue a draft (already ISSUED on create)
- POST   /api/v1/purchases/{id}/cancel/ Cancel with reason
- GET    /api/v1/purchases/returns/   List purchase returns
- POST   /api/v1/purchases/returns/   Create a purchase return
"""

from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.services import get_business
from apps.core.viewsets import TenantModelViewSet

from .api_errors import translate
from .errors import PurchaseError
from .models import Purchase, PurchaseReturn
from .serializers import (
    PurchaseListSerializer,
    PurchaseReturnSerializer,
    PurchaseSerializer,
)
from .services.purchase_service import record_purchase, record_purchase_return

ZERO = "0.00"


class PurchaseViewSet(TenantModelViewSet):
    """
    CRUD for purchase bills, scoped to the logged-in business.

    Purchases are issued immediately on creation (there is no separate
    "issue" step like sales invoices - a supplier bill is already a received
    document). Drafts can be created and edited before issue.
    """

    queryset = Purchase.objects.select_related("party", "business").prefetch_related("items")
    serializer_class = PurchaseSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["bill_number", "party__name"]
    ordering_fields = ["purchase_date", "grand_total", "created_at"]
    ordering = ["-purchase_date", "-id"]

    def get_serializer_class(self):
        if self.action == "list":
            return PurchaseListSerializer
        return PurchaseSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())
        party = self.request.query_params.get("party")
        if party:
            queryset = queryset.filter(party_id=party)
        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(purchase_date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(purchase_date__lte=date_to)
        if self.request.query_params.get("search"):
            term = self.request.query_params["search"].strip()
            from django.db.models import Q

            queryset = queryset.filter(
                Q(bill_number__icontains=term) | Q(party__name__icontains=term)
            )
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_payload = data.pop("items", None)

        business = get_business(request.user)

        try:
            purchase = record_purchase(
                business=business,
                party=data["party"],
                purchase_date=data.get("purchase_date"),
                bill_number=data.get("bill_number", ""),
                place_of_supply=data.get("place_of_supply", ""),
                supply_type=data.get("supply_type", "INTRA"),
                prices_include_tax=data.get("prices_include_tax", False),
                round_invoice_total=False,
                notes=data.get("notes", ""),
                lines=items_payload or [],
                user=request.user if request.user.is_authenticated else None,
            )
        except PurchaseError as exc:
            raise translate(exc) from exc

        return Response(self.get_serializer(purchase).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        purchase = self.get_object()
        if purchase.status != Purchase.Status.DRAFT:
            raise ValidationError(
                {"non_field_errors": ["This purchase bill is issued and can no longer be edited."]}
            )
        serializer = self.get_serializer(
            purchase, data=request.data, partial=kwargs.get("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_payload = data.pop("items", None)
        purchase = serializer.save()

        # Recompute totals after line changes
        from .services.gst_calculator import apply_totals, compute_purchase_totals

        calc_lines = []
        for payload in items_payload or []:
            calc_lines.append(
                {
                    "item_name": payload.get("item_name", ""),
                    "hsn_sac_code": payload.get("hsn_sac_code", ""),
                    "unit": payload.get("unit", ""),
                    "quantity": payload.get("quantity"),
                    "unit_price": payload.get("unit_price"),
                    "line_discount": payload.get("line_discount", "0.00"),
                    "tax_rate": payload.get("tax_rate"),
                }
            )
        totals = compute_purchase_totals(
            business=purchase.business,
            party=purchase.party,
            lines=calc_lines,
            place_of_supply=purchase.place_of_supply,
            supply_type=purchase.supply_type,
        )
        apply_totals(purchase, totals)

        return Response(self.get_serializer(purchase).data)

    def destroy(self, request, *args, **kwargs):
        purchase = self.get_object()
        if purchase.status != Purchase.Status.DRAFT:
            raise ValidationError(
                {"non_field_errors": ["This purchase bill is issued and can no longer be deleted."]}
            )
        purchase.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        purchase = self.get_object()
        reason = request.data.get("reason", "")
        if not reason:
            raise ValidationError({"reason": "A cancellation reason is required."})
        purchase.status = Purchase.Status.CANCELLED
        from django.utils import timezone

        purchase.cancelled_at = timezone.now()
        purchase.cancellation_reason = reason
        purchase.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])
        return Response(self.get_serializer(purchase).data)


class PurchaseReturnViewSet(TenantModelViewSet):
    """
    CRUD for purchase returns (debit notes to suppliers).

    - GET    /api/v1/purchases/returns/
    - POST   /api/v1/purchases/returns/
    - GET    /api/v1/purchases/returns/{id}/
    """

    queryset = PurchaseReturn.objects.select_related("purchase", "business")
    serializer_class = PurchaseReturnSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["return_number", "purchase__bill_number"]
    ordering_fields = ["return_date", "grand_total", "created_at"]
    ordering = ["-return_date", "-id"]

    def get_queryset(self):
        queryset = super().get_queryset()
        purchase = self.request.query_params.get("purchase")
        if purchase:
            queryset = queryset.filter(purchase_id=purchase)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lines_payload = request.data.get("lines", [])

        business = get_business(request.user)
        purchase = data["purchase"]

        try:
            purchase_return = record_purchase_return(
                business=business,
                purchase=purchase,
                return_date=data.get("return_date"),
                return_number=data.get("return_number", ""),
                reason=data.get("reason", ""),
                lines=lines_payload,
                user=request.user if request.user.is_authenticated else None,
            )
        except PurchaseError as exc:
            raise translate(exc) from exc

        return Response(self.get_serializer(purchase_return).data, status=status.HTTP_201_CREATED)
