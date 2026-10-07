"""Views for the Inventory app."""

from django.db.models import Q
from rest_framework import filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.constants import GST_RATE_CHOICES, MEASURING_UNITS, gst_rate_label
from apps.core.viewsets import TenantModelViewSet

from .models import Item
from .serializers import ItemListSerializer, ItemSerializer


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