"""Views for the Inventory app."""

from decimal import Decimal

from django.db.models import Q
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.constants import GST_RATE_CHOICES, MEASURING_UNITS, gst_rate_label
from apps.core.errors import StockError
from apps.core.viewsets import TenantModelViewSet, TenantReadOnlyViewSet

from .models import Item
from .models_stock import StockMovement
from .serializers import ItemImageUploadSerializer, ItemListSerializer, ItemSerializer
from .serializers_stock import (
    StockMovementSerializer,
    filter_movements,
    movement_list_response,
    parse_limit,
)
from .services.stock import adjust_stock


class ItemImageUploadView(APIView):
    """
    PUT /api/v1/items/{id}/image/ uploads or replaces the item photo.
    DELETE /api/v1/items/{id}/image/ removes it.

    Kept separate from the item write endpoint (like the business logo) so image
    validation happens in exactly one place and the field stays read-only on the
    normal serializer.
    """

    parser_classes = [MultiPartParser]

    def _get_item(self, pk):
        # Scoped to the caller's business: another tenant's id is a 404.
        return Item.objects.get(pk=pk, business=self._business())

    def _business(self):
        from apps.accounts.services import get_business

        return get_business(self.request.user)

    def put(self, request, pk):
        item = self._get_item(pk)
        serializer = ItemImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Replace rather than accumulate: drop the old file so storage is not
        # left filling with orphans (see FUTURE_CHECKLIST §7).
        if item.image:
            item.image.delete(save=False)
        item.image = serializer.validated_data["image"]
        item.save(update_fields=["image", "updated_at"])
        return Response(ItemSerializer(item).data)

    def delete(self, request, pk):
        item = self._get_item(pk)
        if item.image:
            item.image.delete(save=False)
            item.image = None
            item.save(update_fields=["image", "updated_at"])
        return Response(ItemSerializer(item).data)


class UnitListView(APIView):
    """
    GET /api/v1/meta/units/ -> [{"code": "PCS", "name": "Pieces"}, ...]

    Served from apps.core.constants so the dropdown can never drift away from
    the choices the model actually accepts.
    """

    def get(self, request):
        return Response([{"code": code, "name": name} for code, name in MEASURING_UNITS])


class GstRateListView(APIView):
    """GET /api/v1/meta/gst-rates/ -> [{"value": "18.00", "label": "18%"}, ...]"""

    def get(self, request):
        return Response(
            [{"value": str(rate), "label": gst_rate_label(rate)} for rate, _ in GST_RATE_CHOICES]
        )


def _stock_error_response(exc: StockError):
    """Stock refusals use the project's error envelope, like every other gate."""
    return Response(
        {
            "success": False,
            "error": exc.code,
            "message": exc.message,
            "errors": {exc.field: [exc.message]} if exc.field else {},
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class ItemViewSet(TenantModelViewSet):
    """
    CRUD for Items (Products & Services), scoped to the logged-in business.

    Endpoints:
    - GET    /api/v1/items/            List (filters: item_type, search, is_active, low_stock)
    - POST   /api/v1/items/            Create
    - GET    /api/v1/items/{id}/       Retrieve
    - PUT    /api/v1/items/{id}/       Full update
    - PATCH  /api/v1/items/{id}/       Partial update
    - DELETE /api/v1/items/{id}/       Soft delete (sets is_active=False)
    - POST   /api/v1/items/{id}/restore/   Restore a soft-deleted item
    - GET    /api/v1/items/search/     Lightweight autocomplete
    """

    queryset = Item.objects.all()
    serializer_class = ItemSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "item_code", "barcode", "hsn_sac_code"]
    ordering_fields = ["name", "item_type", "sales_price", "current_stock", "created_at"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return ItemListSerializer
        return ItemSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # `?negative_stock=true` lists the items that actually need attention:
        # tracked items whose balance is below zero (decision 33's ALLOW case).
        # Untracked items are excluded on purpose - their balance is meaningless
        # and every untracked service would otherwise show up in a stock alert.
        # Absent or unrecognised values leave the list untouched, so a typo in a
        # filter cannot silently return "no items" and look like good news.
        negative = self.request.query_params.get("negative_stock")
        if negative is not None and negative.lower() in ("true", "1", "yes"):
            queryset = queryset.filter(
                track_stock=True,
                current_stock__lt=Decimal("0"),
            )

        # Filter by item type (PRODUCT / SERVICE)
        item_type = self.request.query_params.get("item_type")
        if item_type:
            queryset = queryset.filter(item_type=item_type.upper())

        # Filter by low stock. Uses the model helper so services can never appear.
        low_stock = self.request.query_params.get("low_stock")
        if low_stock is not None:
            if low_stock.lower() == "true":
                queryset = queryset.low_stock()
            else:
                queryset = queryset.exclude(pk__in=queryset.low_stock().values("pk"))

        # Filter by active state (list shows active items only by default)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")
        elif self.action == "list":
            queryset = queryset.filter(is_active=True)

        return queryset

    def perform_destroy(self, instance: Item):
        """Soft delete: set is_active=False instead of hard delete.

        A hard delete would break historic invoice lines that reference this item.
        """
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Restore a soft-deleted item: POST /api/v1/items/{id}/restore/"""
        item = self.get_object()
        item.is_active = True
        item.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(item).data)

    # `url_path` is explicit on both: DRF derives the URL from the method name,
    # which would give `adjust_stock` and `stock_history` with underscores. Every
    # other action in this project uses hyphens (`next_number`, `copy`, `issue`),
    # so the endpoints are pinned to match rather than to the method name.
    @action(detail=True, methods=["get"], url_path="stock-history")
    def stock_history(self, request, pk=None):
        """
        GET /api/v1/items/{id}/stock-history/

        Every movement that produced the current balance, newest first, with the
        running balance on each row - which is the answer to "why is it 7?".
        """
        item = self.get_object()
        business = self.get_business()

        movements = (
            StockMovement.objects.filter(business=business, item=item)
            .select_related("created_by")
            .order_by("-created_at", "-id")
        )
        movement_type = request.query_params.get("movement_type")
        if movement_type:
            movements = movements.filter(movement_type=movement_type)

        return Response(
            {
                "success": True,
                "item_id": item.pk,
                "current_stock": str(item.current_stock) if item.current_stock is not None else None,
                "count": movements.count(),
                "movements": [
                    {
                        "id": movement.pk,
                        "movement_type": movement.movement_type,
                        "quantity_change": str(movement.quantity_change),
                        "stock_after": str(movement.stock_after),
                        "source_type": movement.source_type,
                        "source_label": movement.source_label,
                        "reason": movement.reason,
                        "note": movement.note,
                        "created_at": movement.created_at,
                        "created_by": (
                            movement.created_by.email if movement.created_by_id else None
                        ),
                    }
                    for movement in movements[:200]
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="adjust-stock")
    def adjust_stock(self, request, pk=None):
        """
        POST /api/v1/items/{id}/adjust-stock/ - record a manual correction.

        `mode` is SET (make the balance equal `quantity`) or CHANGE (move it by
        `quantity`). Decision 34: `current_stock` is never edited directly,
        because doing so would silently bypass the ledger and `reconcile_stock`
        would then fail for ever.
        """
        item = self.get_object()
        business = self.get_business()

        mode = str(request.data.get("mode") or "SET").upper()
        raw_quantity = request.data.get("quantity")
        if raw_quantity in (None, ""):
            raise StockError(
                "STOCK_QUANTITY_REQUIRED", "Send the new or changed quantity.",
                field="quantity",
            )

        try:
            movement = adjust_stock(
                business,
                item,
                mode=mode,
                quantity=raw_quantity,
                reason=str(request.data.get("reason") or ""),
                note=str(request.data.get("note") or ""),
                user=request.user,
            )
        except StockError as exc:
            raise _stock_error_response(exc) from exc

        item.refresh_from_db()
        return Response(
            {
                "success": True,
                "item_id": item.pk,
                "current_stock": str(item.current_stock) if item.current_stock is not None else None,
                # None when the correction was a no-op, which the UI shows as
                # "nothing to change" rather than an error.
                "movement": (
                    None
                    if movement is None
                    else {
                        "id": movement.pk,
                        "quantity_change": str(movement.quantity_change),
                        "stock_after": str(movement.stock_after),
                        "reason": movement.reason,
                    }
                ),
            }
        )

    @action(detail=False, methods=["get"])
    def movements(self, request):
        """
        Back-compat alias for the canonical business-wide list.

        The ledger is not an Item sub-resource - it has its own lifecycle
        (append-only, no create/update/delete) - so the documented endpoint is
        `GET /api/v1/stock/movements/`, served by `StockMovementViewSet`. This
        action is kept because the item-scoped history page links here, and a
        silently-removed URL is worse than a redundant one.
        """
        from .serializers_stock import movement_list_response

        return Response(
            movement_list_response(
                StockMovement.objects.filter(business=self.get_business()).select_related(
                    "item"
                ),
request.query_params,
                require_item=False,
            )
        )

    @action(detail=False, methods=["get"])
    def search(self, request):
        """
        Lightweight search for the 1.4 billing form.
        GET /api/v1/items/search/?q=term&item_type=PRODUCT
        Returns: [{id, name, item_code, unit, sales_price, tax_rate, tracks_stock, current_stock}, ...]
        """
        q = request.query_params.get("q", "").strip()
        item_type = request.query_params.get("item_type")

        queryset = self.get_queryset().filter(is_active=True)

        if q:
            queryset = queryset.filter(
                Q(name__icontains=q)
                | Q(item_code__icontains=q)
                | Q(barcode__icontains=q)
                | Q(hsn_sac_code__icontains=q)
            )

        if item_type:
            queryset = queryset.filter(item_type=item_type.upper())

        queryset = queryset[:20]  # limit for autocomplete

        return Response(
            [
                {
                    "id": item.id,
                    "name": item.name,
                    "item_type": item.item_type,
                    "item_code": item.item_code,
                    "unit": item.unit,
                    "sales_price": item.sales_price,
                    "tax_rate": item.tax_rate,
                    "tracks_stock": item.tracks_stock,
                    "current_stock": item.current_stock,
                    "is_low_stock": item.is_low_stock,
                }
                for item in queryset
            ]
        )

class StockMovementViewSet(TenantReadOnlyViewSet):
    """
    `GET /api/v1/stock/movements/` - the business-wide ledger.

    Read-only by construction, and that is deliberate rather than an oversight.
    The ledger is append-only: a movement is evidence that stock moved, and
    editing or deleting one rewrites history. Every model-level guard (the model
    `save()`/`delete()` checks and the queryset override) protects the table from
    mistakes made *inside* Django; exposing a write route would reopen it from
    outside. Correcting a mistake is done by recording another movement, which is
    what `POST /items/{id}/adjust-stock/` is for.

This is a `TenantReadOnlyViewSet`, not `TenantModelViewSet`, so it has no
    create/update/delete routes to accidentally inherit.
    """

    queryset = StockMovement.objects.select_related("item")
    serializer_class = StockMovementSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = super().get_queryset().filter(business=self.get_business())
        queryset = filter_movements(queryset, self.request.query_params, require_item=False)
        return queryset.order_by("-created_at", "-id")[: parse_limit(self.request.query_params)]

    def list(self, request, *args, **kwargs):
        """
        Return the shared ledger envelope rather than DRF's default list body.

        The frontend history drawer and the movements page read the same keys, and
        `count` (total matching) vs `returned` (this page) is what lets the drawer
        say "showing 200 of 1,438".
        """
        return Response(
            movement_list_response(
                StockMovement.objects.filter(business=self.get_business()).select_related(
                    "item"
                ),
request.query_params,
                require_item=False,
            )
        )
