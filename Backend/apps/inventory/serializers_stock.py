"""
Shared shaping for the stock ledger's read endpoints.

The per-item history (`GET /items/{id}/stock-history/`) and the business-wide
list (`GET /stock/movements/`) differ only in their default filter, so they share
one filter/serialise path. Keeping the logic here means a filter added to one
cannot silently be missing from the other - which is how a "the movements page
doesn't show cancellations" bug happens.
"""

from datetime import date

from rest_framework import serializers

from .models_stock import StockMovement

# Enough for the history drawer to be useful in one request. Beyond this the
# frontend pages; the cap exists so a shop with years of movements cannot ask
# for an unbounded response.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

MOVEMENT_FILTERS = ("movement_type", "source_type", "reason", "item")


def _as_date(raw):
    """Parse a `YYYY-MM-DD` filter value, ignoring nonsense rather than 500."""
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def filter_movements(queryset, params, *, require_item: bool):
    """
    Apply the shared query-string filters.

    `item_id` is honoured when given but ignored when not, so
    `/stock/movements/` is business-wide by default. `require_item` exists only
    to make the caller's intent explicit at the call site.
    """
    del require_item  # documented above; both endpoints accept `item`

    item_id = params.get("item") or params.get("item_id")
    if item_id:
        queryset = queryset.filter(item_id=item_id)

    for field in ("movement_type", "source_type", "reason"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{field: value.upper()})

    for param, lookup in (("date_from", "created_at__date__gte"),
                          ("date_to", "created_at__date__lte")):
        value = _as_date(params.get(param))
        if value is not None:
            queryset = queryset.filter(**{lookup: value})

    return queryset


def parse_limit(params):
    """Clamp `limit` to something a browser can actually render."""
    raw = params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        return max(1, min(MAX_LIMIT, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def movement_to_dict(movement):
    """
    One ledger row.

    `quantity_change` is signed and `stock_after` is the running balance, which
    is what makes the drawer readable: the user reads down the column and the
    numbers tell the story without any arithmetic. Amounts are strings, not
    floats - a JSON number cannot represent 0.1 exactly, and a stock ledger that
    says 0.30000000000000004 is worse than no ledger.
    """
    return {
        "id": movement.pk,
        "item_id": movement.item_id,
        "item_code": getattr(movement.item, "item_code", None),
        "item_name": movement.item.name if movement.item_id else None,
        "unit": getattr(movement.item, "unit", None),
        "movement_type": movement.movement_type,
        "quantity_change": str(movement.quantity_change),
        "stock_after": str(movement.stock_after),
        "reason": movement.reason,
        "source_type": movement.source_type,
        "source_label": movement.source_label,
"source_line_id": movement.source_line_id,
        "source_id": movement.source_id,
        "note": movement.note,
        "created_at": movement.created_at,
    }


def movement_list_response(queryset, params, *, require_item: bool):
    """Filter, order, clamp and serialise. Returns the dict, not a Response."""
    queryset = filter_movements(queryset, params, require_item=require_item)
    total = queryset.count()
    rows = list(queryset.order_by("-created_at", "-id")[: parse_limit(params)])
    return {
        "success": True,
        "count": total,
        "returned": len(rows),
        "limit": parse_limit(params),
        "movements": [movement_to_dict(row) for row in rows],
    }

class StockMovementSerializer(serializers.ModelSerializer):
    """
    Read-only projection of a ledger row.

    Every field is `read_only`. There is no write serializer for this model at
    all - movements are produced by the stock service, never by a request body -
    so this class exists only to give DRF something to document the shape with,
    and to back `GET /stock/movements/{id}/`.
    """

    item_name = serializers.CharField(source="item.name", read_only=True)
    item_code = serializers.CharField(source="item.item_code", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "item",
            "item_name",
            "item_code",
            "movement_type",
            "quantity_change",
            "stock_after",
            "reason",
            "source_type",
            "source_label",
"source_line_id",
            "source_id",
            "note",
            "created_at",
        ]
        read_only_fields = fields
