"""
Views for invoices.

This step covers DRAFT CRUD plus `/preview/`. Issue, cancel and numbering are
step D, so the lifecycle actions are deliberately absent here.

`/preview/` and invoice persistence both call
`apps/invoices.services.draft.compute_totals()`, so the numbers the browser
shows before saving are produced by the same code that writes them.
"""

from datetime import datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.services import get_business
from apps.core.fiscal import financial_year
from apps.core.viewsets import TenantModelViewSet

from .api_errors import translate, translate_calculation
from .models import Invoice, InvoiceItem
from .serializers import (
    InvoiceListSerializer,
    InvoicePreviewSerializer,
    InvoiceSerializer,
)
from .services.draft import (
    apply_totals,
    build_line_from_payload,
    compute_totals,
    preview_hsn_summary,
    resolve_place_of_supply,
)
from .services.gst_calculator import GSTCalculationError
from .services.issue import IssueError, cancel_invoice, issue_invoice
from .services.numbering import peek_next_number


class InvoiceViewSet(TenantModelViewSet):
    """
    CRUD for invoices, scoped to the logged-in business.

    - GET    /api/v1/invoices/            List (status, party, date range, search)
    - POST   /api/v1/invoices/            Create a DRAFT with nested lines
    - GET    /api/v1/invoices/{id}/       Retrieve
    - PATCH  /api/v1/invoices/{id}/       Edit - DRAFT only
    - DELETE /api/v1/invoices/{id}/       Delete - DRAFT only
    - POST   /api/v1/invoices/preview/    Recalculate without saving
    - POST   /api/v1/invoices/{id}/copy/  New draft from any invoice
    """

    queryset = Invoice.objects.select_related("party", "business").prefetch_related("items")
    serializer_class = InvoiceSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["invoice_number", "party__name"]
    ordering_fields = ["invoice_date", "grand_total", "created_at"]
    ordering = ["-invoice_date", "-id"]

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        if self.action == "preview":
            return InvoicePreviewSerializer
        return InvoiceSerializer

    def _apply_filters(self, queryset, params):
        """Shared by the list view and the `stats` action so the tiles always
        describe exactly the rows the table is showing."""
        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())

        supply = params.get("supply_type")
        if supply:
            queryset = queryset.filter(supply_type=supply.upper())

        party = params.get("party")
        if party:
            queryset = queryset.filter(party_id=party)

        # Inclusive date range; both ends optional.
        date_from = params.get("date_from")
        if date_from:
            queryset = queryset.filter(invoice_date__gte=date_from)
        date_to = params.get("date_to")
        if date_to:
            queryset = queryset.filter(invoice_date__lte=date_to)

        # Drafts have no invoice number yet, so searching by number would
        # otherwise hide every draft.
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(
                Q(invoice_number__icontains=term) | Q(party__name__icontains=term)
            )
        return queryset

    def get_queryset(self):
        return self._apply_filters(super().get_queryset(), self.request.query_params)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """
        GET /api/v1/invoices/stats/ - totals for the current filters.

        Computed on the server because the list is paginated: totalling the rows
        in the browser would silently describe only the visible page, which is
        exactly the kind of quiet inaccuracy this project avoids elsewhere.
        """
        queryset = self._apply_filters(self.get_queryset(), request.query_params)
        aggregates = queryset.aggregate(
            count=Count("id"),
            draft_count=Count("id", filter=Q(status=Invoice.Status.DRAFT)),
            issued_count=Count("id", filter=Q(status=Invoice.Status.ISSUED)),
            cancelled_count=Count("id", filter=Q(status=Invoice.Status.CANCELLED)),
            taxable_total=Sum("taxable_total"),
            cgst_total=Sum("cgst_total"),
            sgst_total=Sum("sgst_total"),
            igst_total=Sum("igst_total"),
            grand_total=Sum("grand_total"),
        )
        tax_total = sum(
            aggregates[key] or Decimal("0")
            for key in ("cgst_total", "sgst_total", "igst_total")
        )
        return Response(
            {
                "count": aggregates["count"] or 0,
                "draft_count": aggregates["draft_count"] or 0,
                "issued_count": aggregates["issued_count"] or 0,
                "cancelled_count": aggregates["cancelled_count"] or 0,
                "taxable_total": str(aggregates["taxable_total"] or Decimal("0")),
                "tax_total": str(tax_total),
                "grand_total": str(aggregates["grand_total"] or Decimal("0")),
            }
        )

    @property
    def throttle_scope(self):
        """
        `/preview/` runs the whole GST calculation on every keystroke-debounce, so
        it gets its own tighter rate (120/min in settings).

        A property rather than a decorator kwarg: DRF rejects unknown kwargs on
        `@action`, and ScopedRateThrottle reads this attribute at request time.
        """
        return "preview" if self.action == "preview" else None

    def get_throttles(self):
        # ScopedRateThrottle alone here, so the preview is not also charged
        # against the general per-user rate.
        if self.action == "preview":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def _business(self):
        return get_business(self.request.user)

    def _save_lines(self, invoice, items_payload):
        """Replace the invoice's lines with the submitted set."""
        invoice.items.all().delete()
        for index, payload in enumerate(items_payload):
            try:
                line = build_line_from_payload(payload, business=invoice.business)
            except ValueError as exc:
                # A bad line is the caller's input problem, not a server fault.
                # Report it against the offending line so the UI can highlight it.
                raise ValidationError({f"items.{index}": [str(exc)]}) from exc
            line.invoice = invoice
            line.save()

    def _recalculate(self, invoice):
        """Run the single shared calculation and persist its result."""
        try:
            totals = compute_totals(
                business=invoice.business,
                party=invoice.party,
                lines=list(invoice.items.all()),
                place_of_supply=invoice.place_of_supply,
                invoice_discount=invoice.invoice_discount,
                prices_include_tax=invoice.prices_include_tax,
                round_invoice_total=invoice.business.round_invoice_total,
            )
        except GSTCalculationError as exc:
            raise translate_calculation(exc) from exc
        return apply_totals(invoice, totals)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # DRF cannot write nested fields itself; the lines are built by
        # _save_lines() so the service layer controls their snapshots.
        items_payload = serializer.validated_data.pop("items", None)

        invoice = serializer.save(
            business=self._business(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        self._save_lines(invoice, items_payload or [])
        self._recalculate(invoice)

        return Response(self.get_serializer(invoice).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        invoice = self.get_object()
        self._assert_draft(invoice)

        serializer = self.get_serializer(
            invoice, data=request.data, partial=kwargs.get("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        items_payload = serializer.validated_data.pop("items", None)
        invoice = serializer.save()

        if items_payload is not None:
            self._save_lines(invoice, items_payload)
        self._recalculate(invoice)

        return Response(self.get_serializer(invoice).data)

    def destroy(self, request, *args, **kwargs):
        invoice = self.get_object()
        self._assert_draft(invoice)
        invoice.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _assert_draft(self, invoice: Invoice) -> None:
        """Never a silent no-op on an issued invoice."""
        if invoice.status != Invoice.Status.DRAFT:
            raise ValidationError(
                {
                    "non_field_errors": [
                        f"Invoice {invoice.display_number} is {invoice.get_status_display().lower()} "
                        "and can no longer be edited or deleted."
                    ]
                }
            )

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        POST /api/v1/invoices/preview/ - recalculate without saving.

        The ONLY place GST maths runs for the UI. Runs the identical
        `compute_totals()` used when an invoice is saved, so the preview cannot
        drift from the saved document.
        """
        # context is mandatory, not optional: TenantPrimaryKeyRelatedField reads
        # the caller from it to scope party/item lookups, and fails closed
        # (every id "does not exist") when it is missing.
        serializer = InvoicePreviewSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        business = self._business()
        party = data["party"]
        items_payload = data["items"]

        # Build throwaway lines so the calculator sees the same inputs a save
        # would produce (item snapshots filled in, defaults applied).
        lines = []
        for payload in items_payload:
            try:
                line = build_line_from_payload(payload, business=business)
            except ValueError as exc:
                raise ValidationError({"items": [str(exc)]}) from exc
            line.pk = None
            lines.append(line)

        try:
            totals = compute_totals(
                business=business,
                party=party,
                lines=lines,
                place_of_supply=data.get("place_of_supply") or "",
                invoice_discount=data.get("invoice_discount") or "0.00",
                prices_include_tax=data.get("prices_include_tax", False),
                round_invoice_total=business.round_invoice_total,
            )
        except GSTCalculationError as exc:
            raise translate_calculation(exc) from exc

        return Response(
            {
                "totals": {
                    "subtotal": str(totals.subtotal),
                    "total_discount": str(totals.total_discount),
                    "taxable_total": str(totals.taxable_total),
                    "cgst_total": str(totals.cgst_total),
                    "sgst_total": str(totals.sgst_total),
                    "igst_total": str(totals.igst_total),
                    "round_off": str(totals.round_off),
                    "grand_total": str(totals.grand_total),
                    "tax_total": str(totals.tax_total),
                },
                "supply_type": totals.supply_type,
                "state_tax_label": totals.state_tax_label,
                "place_of_supply": data.get("place_of_supply")
                or resolve_place_of_supply(party, lines, business),
                "document_title": totals.document_title,
                "lines": totals.as_dicts(),
                "hsn_summary": [
                    {k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()}
                    for row in preview_hsn_summary(lines, totals)
                ],
            }
        )

    # ------------------------------------------------------------------
    # Lifecycle: issue / cancel
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """
        POST /api/v1/invoices/{id}/issue/ - DRAFT becomes a numbered document.

        Idempotent by design: issuing an already-issued invoice returns 200 with
        the same number rather than burning a second one, so a double-click or
        a retried request cannot leave a gap in the series.
        """
        invoice = self.get_object()  # 404 for another tenant's invoice
        try:
            issued, already = issue_invoice(invoice.pk, user=request.user)
        except IssueError as exc:
            raise translate(exc) from exc

        return Response(
            self.get_serializer(issued).data,
            status=status.HTTP_200_OK if already else status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        POST /api/v1/invoices/{id}/cancel/ - requires a reason.

        The number is never freed: if that period's GSTR-1 is already filed the
        correct instrument is a credit note, not a cancellation.
        """
        invoice = self.get_object()
        reason = request.data.get("reason", "")
        try:
            cancelled = cancel_invoice(
                invoice.pk, reason=reason, user=request.user
            )
        except IssueError as exc:
            raise translate(exc) from exc
        return Response(self.get_serializer(cancelled).data)

    @action(detail=False, methods=["get"])
    def next_number(self, request):
        """
        GET /api/v1/invoices/next-number/?date=YYYY-MM-DD

        What the next issue will get, without consuming it. Uses the FY
        counter's own snapshotted prefix, so this can never disagree with what
        is actually issued.
        """
        business = self._business()
        raw_date = request.query_params.get("date")
        try:
            on_date = (
                datetime.strptime(raw_date, "%Y-%m-%d").date()
                if raw_date
                else timezone.localdate()
            )
        except ValueError as exc:
            raise ValidationError({"date": "Use YYYY-MM-DD."}) from exc
        return Response(
            {
                "date": on_date.isoformat(),
                "financial_year": financial_year(on_date),
                "next_number": peek_next_number(business, on_date),
            }
        )

    # ------------------------------------------------------------------
    # Copy
    # ------------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        """
        POST /api/v1/invoices/{id}/copy/ - a new DRAFT from any invoice.

        Makes cancel-and-reissue painless: nothing is retyped, and the copied
        draft re-prices from the *current* catalogue rather than the old snapshot.
        """
        source = self.get_object()
        business = self._business()

        draft = Invoice.objects.create(
            business=business,
            party=source.party,
            invoice_date=source.invoice_date,
            due_date=source.due_date,
            prices_include_tax=source.prices_include_tax,
            notes=source.notes,
            terms=source.terms,
            created_by=request.user if request.user.is_authenticated else None,
        )
        for line in source.items.all():
            InvoiceItem.objects.create(
                invoice=draft,
                # From the PARENT invoice, never from the request. Both are the
                # same tenant today, but taking it from the parent makes it
                # structurally impossible for a line to disagree with its
                # invoice about who owns it.
                business=draft.business,
                item=line.item,
                item_name=line.item_name,
                item_type=line.item_type,
                hsn_sac_code=line.hsn_sac_code,
                unit=line.unit,
                service_description=line.service_description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_discount=line.line_discount,
                tax_rate=line.tax_rate,
            )
        draft.invoice_discount = source.invoice_discount
        draft.save(update_fields=["invoice_discount", "updated_at"])

        self._recalculate(draft)
        return Response(self.get_serializer(draft).data, status=status.HTTP_201_CREATED)
