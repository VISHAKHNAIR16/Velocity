"""
Purchase API.

Mirrors `apps/invoices/views.py` where the shape matches - the same envelope, the
same throttle treatment on the preview action, the same tenant scoping - so a
developer moving between the two does not have to re-learn anything.
"""

from decimal import Decimal

from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.core.errors import StockError
from apps.core.viewsets import TenantModelViewSet
from apps.invoices.errors import IssueError
from apps.invoices.services.gst_calculator import LineInput, calculate_invoice
from apps.purchases.errors import PurchaseConflict, PurchaseError, translate_purchase
from apps.purchases.models import Purchase
from apps.purchases.serializers import (
    PurchaseListSerializer,
    PurchaseSerializer,
)
from apps.purchases.services.divergence import build_report
from apps.purchases.services.draft import build_line_from_payload
from apps.purchases.services.lifecycle import cancel_purchase, post_purchase


class PurchaseViewSet(TenantModelViewSet):
    """
    `GET/POST /api/v1/purchases/`, plus
    `POST /api/v1/purchases/{id}/post/` and `/cancel/`.
    """

    queryset = Purchase.objects.select_related("supplier").prefetch_related("items")
    serializer_class = PurchaseSerializer
    filterset_fields = ["status", "supplier"]

    def get_serializer_class(self):
        if self.action == "list":
            return PurchaseListSerializer
        if self.action == "preview":
            return PurchaseSerializer
        return PurchaseSerializer

    def get_throttles(self):
        # The preview is the same shape of problem as the invoice preview - a
        # full recalculation driven by typing - so it gets the same scoped rate
        # rather than the looser default.
        if self.action == "preview":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        queryset = super().get_queryset()

        bill_date = self.request.query_params.get("bill_date")
        if bill_date:
            queryset = queryset.filter(bill_date=bill_date)

        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(bill_date__gte=date_from)

        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(bill_date__lte=date_to)

        # "Bills whose figures did not agree with the calculator" - the stored
        # computed mirror exists precisely so this is a cheap filter rather than
        # a recalculation of the whole year.
        if self.request.query_params.get("divergent") in ("true", "1", "yes"):
            queryset = queryset.filter(has_divergence=True)

        return queryset

    def _translate(self, exc):
        code, body = translate_purchase(exc)
        return Response(body, status=code)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="post")
    def post_bill(self, request, pk=None):
        """
        `POST /api/v1/purchases/{id}/post/` - DRAFT becomes POSTED and stock rises.

        The method is named `post_bill` only so it does not shadow the HTTP verb
        inside this class; the URL segment is `post`, matching the spec's wording.
        """
        try:
            purchase, already = post_purchase(pk, user=request.user)
        except (PurchaseError, PurchaseConflict, StockError, IssueError) as exc:
            return self._translate(exc)
        except IntegrityError:
            # The duplicate constraint firing means two requests raced past the
            # friendly check. Say the useful thing rather than the SQL.
            return Response(
                {
                    "success": False,
                    "error": "DUPLICATE_BILL_NUMBER",
                    "message": "That supplier bill number is already recorded for this year.",
                    "errors": {"bill_number": ["Already recorded."]},
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {"success": True, "already_posted": already, "purchase": PurchaseSerializer(purchase).data},
            status=status.HTTP_200_OK if already else status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        `POST /api/v1/purchases/{id}/cancel/` - reverse the stock increase.

        A reason is required. "Cancelled a purchase" with no reason is not
        something anyone can audit six months later, and the ledger entry has to
        say why.
        """
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {
                    "success": False,
                    "error": "REASON_REQUIRED",
                    "message": "Say why this bill is being cancelled.",
                    "errors": {"reason": ["Required."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            purchase = cancel_purchase(pk, reason=reason, user=request.user)
        except (PurchaseError, PurchaseConflict, StockError, IssueError) as exc:
            # CANCEL_WOULD_GO_NEGATIVE lands here, with `details` naming the items.
            return self._translate(exc)
        return Response(
            {"success": True, "purchase": PurchaseSerializer(purchase).data}
        )

    @action(detail=True, methods=["get"])
    def stock_effect(self, request, pk=None):
        """
        `GET /api/v1/purchases/{id}/stock-effect/` - what posting would move.

        Advisory, like the invoice preview's stock check: no lock, no write. It
        exists so the user can see the effect before committing, not to decide
        anything.
        """
        purchase = self.get_object()
        from apps.inventory.models import Item
        from apps.inventory.services.stock import _stock_lines_for_document

        lines = _stock_lines_for_document(purchase)
        names = dict(
            Item.objects.filter(pk__in=[line.item_id for line in lines]).values_list(
                "pk", "name"
            )
        )
        return Response(
            {
                "success": True,
                "moves_stock": bool(lines),
                "lines": [
                    {
                        "item_id": line.item_id,
                        "item_name": names.get(line.item_id, ""),
                        "quantity": str(line.quantity),
                    }
                    for line in lines
                ],
            }
        )

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        `POST /api/v1/purchases/preview/` - what the calculator makes of this bill.

        Advisory, non-locking, and **writes nothing at all** - not even a
        temporary row. The lines are built in memory and the entered figures are
        compared against the calculator's, so the user sees the difference
        before anything is saved and before the bill counts as a duplicate.

        Nothing is overwritten: the whole point is to tell the user their typed
        figures differ from the arithmetic, not to fix them.
        """
        serializer = PurchaseSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)

        business = serializer._business()
        supplier = serializer.validated_data["supplier"]
        items = serializer.validated_data.get("items", [])

        origin = (
            serializer.validated_data.get("origin_state_code")
            or supplier.state_code
            or ""
        )
        place_of_supply = (
            serializer.validated_data.get("place_of_supply") or business.state_code or ""
        )

        lines = [build_line_from_payload(row, business=business) for row in items]

        entered = _EnteredBill(serializer.validated_data, lines)
        computed = _compute_totals(
            lines,
            origin_state=origin,
            place_of_supply=place_of_supply,
            supplier_is_regular=bool(supplier.gstin),
            prices_include_tax=entered.prices_include_tax,
        )

        report = build_report(entered, computed)

        return Response(
            {
                "success": True,
                "supply_type": computed.supply_type if computed else None,
                "state_tax_label": computed.state_tax_label if computed else None,
                "computed": {
                    "taxable_total": str(getattr(computed, "taxable_total", ZERO)),
                    "cgst_total": str(getattr(computed, "cgst_total", ZERO)),
                    "sgst_total": str(getattr(computed, "sgst_total", ZERO)),
                    "igst_total": str(getattr(computed, "igst_total", ZERO)),
                    "grand_total": str(getattr(computed, "grand_total", ZERO)),
                },
                "entered": {
                    key: str(getattr(entered, key, ZERO))
                    for key in (
                        "taxable_total",
                        "cgst_total",
                        "sgst_total",
                        "igst_total",
                    )
                },
                "has_divergence": report.has_divergence,
                "max_divergence": str(report.max_difference),
                "divergences": [
                    {
                        "field": d.field,
                        "entered": str(d.entered),
                        "computed": str(d.computed),
                        "difference": str(d.difference),
                    }
                    for d in report.material
                ],
                "summary": report.summary(),
            }
        )


ZERO = Decimal("0.00")


class _EnteredBill:
    """
    The entered side of the comparison, held in memory.

    A throwaway stand-in for `Purchase` so `divergence.build_report` can read it
    with the same `getattr` calls it uses on a real row. Using the model instead
    would mean saving a draft just to preview it, which is exactly what a preview
    must not do.
    """

    def __init__(self, payload: dict, lines: list):
        for key in (
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
        ):
            setattr(self, key, payload.get(key) or ZERO)
        self.prices_include_tax = bool(payload.get("prices_include_tax"))
        self.items = lines


def _compute_totals(lines, *, origin_state, place_of_supply, supplier_is_regular, prices_include_tax):
    """Run the calculator for a purchase without saving anything."""
    if not lines:
        return None
    inputs = [
        LineInput.build(
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_rate=line.tax_rate,
            line_discount=line.line_discount,
            item_type=line.item_type,
        )
        for line in lines
    ]
    return calculate_invoice(
        inputs,
        origin_state=origin_state,
        place_of_supply=place_of_supply,
        business_is_regular=supplier_is_regular,
        prices_include_tax=prices_include_tax,
        round_invoice_total=False,
    )