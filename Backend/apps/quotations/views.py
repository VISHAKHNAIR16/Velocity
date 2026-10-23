"""
Views for quotations.

Endpoints:
- GET    /api/v1/quotations/           List
- POST   /api/v1/quotations/           Create a quotation
- GET    /api/v1/quotations/{id}/      Retrieve
- PATCH  /api/v1/quotations/{id}/      Edit - DRAFT only
- DELETE /api/v1/quotations/{id}/      Delete - DRAFT only
- POST   /api/v1/quotations/{id}/send/  Mark as SENT
- POST   /api/v1/quotations/{id}/convert-to-invoice/  Convert to DRAFT invoice
"""

from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.services import get_business
from apps.core.viewsets import TenantModelViewSet

from .api_errors import translate
from .errors import QuotationError
from .models import Quotation
from .serializers import QuotationListSerializer, QuotationSerializer
from .services.quotation_service import convert_to_invoice, create_quotation


class QuotationViewSet(TenantModelViewSet):
    """CRUD for quotations, scoped to the logged-in business."""

    queryset = Quotation.objects.select_related("party", "business").prefetch_related("items")
    serializer_class = QuotationSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["display_number", "party__name", "customer_name"]
    ordering_fields = ["quotation_date", "grand_total", "created_at"]
    ordering = ["-quotation_date", "-id"]

    def get_serializer_class(self):
        if self.action == "list":
            return QuotationListSerializer
        return QuotationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())
        party = self.request.query_params.get("party")
        if party:
            queryset = queryset.filter(party_id=party)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_payload = data.pop("items", None)

        business = get_business(request.user)

        try:
            quotation = create_quotation(
                business=business,
                party=data["party"],
                quotation_date=data.get("quotation_date"),
                valid_until=data.get("valid_until"),
                place_of_supply=data.get("place_of_supply", ""),
                supply_type=data.get("supply_type", "INTRA"),
                prices_include_tax=data.get("prices_include_tax", False),
                notes=data.get("notes", ""),
                terms=data.get("terms", ""),
                lines=items_payload or [],
                user=request.user if request.user.is_authenticated else None,
            )
        except QuotationError as exc:
            raise translate(exc) from exc

        return Response(self.get_serializer(quotation).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        quotation = self.get_object()
        if quotation.status != Quotation.Status.DRAFT:
            raise ValidationError({"non_field_errors": ["This quotation can no longer be edited."]})
        serializer = self.get_serializer(
            quotation, data=request.data, partial=kwargs.get("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_payload = data.pop("items", None)
        quotation = serializer.save()

        # Recompute totals
        from .services.gst_calculator import apply_totals as apply_qt
        from .services.gst_calculator import compute_quotation_totals

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
        totals = compute_quotation_totals(
            business=quotation.business,
            party=quotation.party,
            lines=calc_lines,
            place_of_supply=quotation.place_of_supply,
            supply_type=quotation.supply_type,
        )
        apply_qt(quotation, totals)

        return Response(self.get_serializer(quotation).data)

    def destroy(self, request, *args, **kwargs):
        quotation = self.get_object()
        if quotation.status != Quotation.Status.DRAFT:
            raise ValidationError(
                {"non_field_errors": ["This quotation can no longer be deleted."]}
            )
        quotation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        quotation = self.get_object()
        if quotation.status != Quotation.Status.DRAFT:
            raise ValidationError({"non_field_errors": ["Only a draft quotation can be sent."]})
        quotation.status = Quotation.Status.SENT
        quotation.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(quotation).data)

    @action(detail=True, methods=["post"], url_path="convert-to-invoice")
    def convert_to_invoice(self, request, pk=None):
        """
        POST /api/v1/quotations/{id}/convert-to-invoice/

        Atomically creates a DRAFT sales invoice from this quotation and marks
        the quotation CONVERTED.
        """
        quotation = self.get_object()
        user = request.user if request.user.is_authenticated else None
        try:
            invoice = convert_to_invoice(quotation, user=user)
        except QuotationError as exc:
            raise translate(exc) from exc

        from apps.invoices.serializers import InvoiceSerializer

        return Response(
            InvoiceSerializer(invoice, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )
