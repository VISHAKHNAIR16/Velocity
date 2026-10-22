"""
Views for the payments app.

Endpoints:
- GET    /api/v1/payments/           List (scoped to business, filterable by party, type, date)
- POST   /api/v1/payments/           Create a payment (recalculates linked invoice)
- GET    /api/v1/payments/{id}/      Retrieve
"""

from rest_framework import filters, status
from rest_framework.response import Response

from apps.accounts.services import get_business
from apps.core.viewsets import TenantModelViewSet

from .api_errors import translate
from .errors import PaymentError
from .models import PaymentRecord
from .serializers import PaymentCreateSerializer, PaymentRecordSerializer


class PaymentRecordViewSet(TenantModelViewSet):
    """
    CRUD for payments, scoped to the logged-in user's business.

    Payments are append-only: there is no PUT/PATCH/DELETE. A payment entered
    in error is corrected by posting a reversing payment.
    """

    queryset = PaymentRecord.objects.select_related("party", "invoice", "business")
    serializer_class = PaymentRecordSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["party__name", "reference_number"]
    ordering_fields = ["payment_date", "amount", "created_at"]
    ordering = ["-payment_date", "-created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return PaymentCreateSerializer
        return PaymentRecordSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by party
        party = self.request.query_params.get("party")
        if party:
            queryset = queryset.filter(party_id=party)

        # Filter by transaction type
        txn_type = self.request.query_params.get("transaction_type")
        if txn_type:
            queryset = queryset.filter(transaction_type=txn_type.upper())

        # Inclusive date range
        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(payment_date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(payment_date__lte=date_to)

        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        business = get_business(request.user)

        # Cross-tenant check: invoice (if linked) must belong to this business
        invoice = data.get("invoice")
        if invoice is not None and invoice.business_id != business.pk:
            from .api_errors import PaymentLifecycleError

            raise PaymentLifecycleError(
                "INVOICE_WRONG_BUSINESS",
                "The invoice belongs to a different business.",
                field="invoice",
            )

        from .services.ledger import record_payment

        try:
            payment = record_payment(
                business=business,
                party=data["party"],
                transaction_type=data["transaction_type"],
                payment_date=data["payment_date"],
                payment_mode=data["payment_mode"],
                amount=data["amount"],
                invoice=invoice,
                reference_number=data.get("reference_number", ""),
                notes=data.get("notes", ""),
            )
        except PaymentError as exc:
            raise translate(exc) from exc

        return Response(
            PaymentRecordSerializer(payment, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """Payments are append-only: edits are refused."""
        from rest_framework.exceptions import MethodNotAllowed

        raise MethodNotAllowed("PUT")

    def partial_update(self, request, *args, **kwargs):
        """Payments are append-only: partial edits are refused."""
        from rest_framework.exceptions import MethodNotAllowed

        raise MethodNotAllowed("PATCH")

    def destroy(self, request, *args, **kwargs):
        """Payments are append-only: deletion is refused."""
        from rest_framework.exceptions import MethodNotAllowed

        raise MethodNotAllowed("DELETE")
