"""
Purchase serializers.

The rule that shapes this file: **money fields are writable, and that is the
opposite of the invoice serializer on purpose.** A purchase's numbers come from
the supplier's bill and are entered as printed. They are not computed here and
not overwritten; the calculator's versions live in the read-only `*_computed`
fields so a discrepancy is visible rather than resolved behind the user's back.

`business` is never writable. The view stamps it from the logged-in user, so a
request body cannot name another tenant - the same rule every other serializer
in this project follows.
"""

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.core.fiscal import financial_year
from apps.invoices.errors import IssueError
from apps.invoices.services.period_rules import assert_period_open
from apps.purchases.models import Purchase, PurchaseItem, PurchaseStatus
from apps.purchases.services.draft import (
    replace_lines,
    snapshot_business,
    snapshot_supplier,
)
from apps.purchases.services.lifecycle import recalculate_draft

ZERO = Decimal("0.00")


class EmptyStringToNullMixin:
    """Treat an empty string as "not supplied" on optional date fields.

    `allow_blank=True` is not understood by DRF's DateField, so declaring it in
    `extra_kwargs` alone changes nothing. This mixin does the actual work, and
    it is applied to the serializer class rather than to the Meta options so
    the behaviour is visible at the point it happens.
    """

    #: Fields to normalise. Subclasses list their own.
    empty_to_null_fields: tuple[str, ...] = ()

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, "copy") else dict(data)
        for field in self.empty_to_null_fields:
            if field in data and data[field] == "":
                data[field] = None
        return super().to_internal_value(data)

#: Header money the client supplies verbatim.
ENTERED_MONEY = (
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "round_off",
    "grand_total",
)

#: The calculator's versions, never writable.
COMPUTED_MONEY = (
    "taxable_total_computed",
    "cgst_total_computed",
    "sgst_total_computed",
    "igst_total_computed",
    "grand_total_computed",
)


class PurchaseItemSerializer(serializers.ModelSerializer):
    """One line. Both sets of figures; only the entered ones are writable."""

    class Meta:
        model = PurchaseItem
        fields = [
            "id",
            "item",
            "item_name",
            "item_type",
            "hsn_sac_code",
            "unit",
            "service_description",
            "quantity",
            "unit_price",
            "line_discount",
            "tax_rate",
            "itc_eligible",
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "total_amount",
            "taxable_value_computed",
            "cgst_amount_computed",
            "sgst_amount_computed",
            "igst_amount_computed",
            "total_amount_computed",
            "tracks_stock",
        ]
        read_only_fields = [
            "id",
            "business",
            "taxable_value_computed",
            "cgst_amount_computed",
            "sgst_amount_computed",
            "igst_amount_computed",
            "total_amount_computed",
            "tracks_stock",
        ]

    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.00"))
    line_discount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00"), default=ZERO
    )
    tax_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=Decimal("0.00"), default=ZERO
    )

    tracks_stock = serializers.BooleanField(read_only=True)
    itc_eligible = serializers.BooleanField(required=False, default=True)

    # These default from the catalogue item when one is given, and are required
    # only for a free-text line - which `validate` checks with a message the user
    # can act on, rather than three separate "this field is required" errors.
    item_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    item_type = serializers.CharField(required=False, max_length=8)
    hsn_sac_code = serializers.CharField(required=False, allow_blank=True, max_length=8)
    unit = serializers.CharField(required=False, allow_blank=True, max_length=8)
    service_description = serializers.CharField(
        required=False, allow_blank=True, max_length=80
    )
    taxable_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    cgst_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    sgst_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    igst_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    total_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )

    def validate(self, attrs):
        quantity = attrs.get("quantity")
        price = attrs.get("unit_price")
        discount = attrs.get("line_discount", ZERO)
        if quantity is not None and price is not None and discount > (quantity * price):
            raise serializers.ValidationError(
                {"line_discount": "The discount cannot be more than the line total."}
            )
        return attrs

    def validate_item(self, value):
        """Tenant-scoped: another business's item id must not resolve."""
        request = self.context.get("request")
        business = self._business()
        if value is not None and business is not None and value.business_id != business.id:
            raise serializers.ValidationError("That item does not exist in your business.")
        if request is not None and value is not None and value.is_active is False:
            # Not fatal - a shop may still be recording a bill for a discontinued
            # item - but posting will refuse it, so say so now rather than later.
            pass
        return value

    def _business(self):
        request = self.context.get("request")
        if request is None:
            return None
        from apps.accounts.services import get_business

        return get_business(request.user)


class PurchaseListSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Purchase
        fields = [
            "id",
            "supplier",
            "supplier_name",
            "bill_number",
            "financial_year",
            "bill_date",
            "status",
            "status_label",
            "subtotal",
            "taxable_total",
            "tax_total",
            "grand_total",
            "has_divergence",
            "max_divergence",
        ]


class PurchaseSerializer(EmptyStringToNullMixin, serializers.ModelSerializer):
    """Full purchase: header, entered money, computed mirror, lines."""

    empty_to_null_fields = ("due_date",)

    items = PurchaseItemSerializer(many=True, required=False)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    supplier_state_code = serializers.CharField(read_only=True)
    is_locked = serializers.SerializerMethodField()

    class Meta:
        model = Purchase
        fields = [
            "id",
            "supplier",
            "bill_number",
            "financial_year",
            "bill_date",
            "due_date",
            "status",
            "status_label",
            "origin_state_code",
            "place_of_supply",
            "supply_type",
            "prices_include_tax",
            "update_item_purchase_price",
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "taxable_total_computed",
            "cgst_total_computed",
            "sgst_total_computed",
            "igst_total_computed",
            "grand_total_computed",
            "max_divergence",
            "has_divergence",
            "supplier_name",
            "supplier_gstin",
            "supplier_state_code",
            "supplier_address",
            "business_name",
            "business_state_code",
            "document_title",
            "notes",
            "terms",
            "posted_at",
            "cancelled_at",
            "cancellation_reason",
            "is_locked",
            "items",
        ]
        # origin_state_code and place_of_supply are defaulted from the supplier
        # and the business in create(); requiring them here would force the client
        # to send values the server already knows.
        extra_kwargs = {
            "origin_state_code": {"required": False},
            "place_of_supply": {"required": False},
            "subtotal": {"required": False},
            "total_discount": {"required": False},
            "taxable_total": {"required": False},
            "cgst_total": {"required": False},
            "sgst_total": {"required": False},
            "igst_total": {"required": False},
            "round_off": {"required": False},
            "grand_total": {"required": False},
            "prices_include_tax": {"required": False},
            "update_item_purchase_price": {"required": False},
            # `allow_null` alone is not enough. A client that sends the *empty
            # string* for an untouched date input is not supplying a value, but
            # DRF reads "" as a malformed date and answers with a message about
            # accepted formats - which looks like the user typed nonsense into
            # the field. `allow_blank` is not an option here: DateField rejects
            # it outright (TypeError). `EmptyStringToNullMixin` does the work.
            "due_date": {"required": False, "allow_null": True},
        }
        read_only_fields = [
            "id",
            "financial_year",
            "status",
            "supply_type",
            "tax_total",
            "taxable_total_computed",
            "cgst_total_computed",
            "sgst_total_computed",
            "igst_total_computed",
            "grand_total_computed",
            "max_divergence",
            "has_divergence",
            "supplier_name",
            "supplier_gstin",
            "supplier_state_code",
            "supplier_address",
            "business_name",
            "business_state_code",
            "posted_at",
            "cancelled_at",
            "cancellation_reason",
            "is_locked",
        ]

    def get_is_locked(self, obj) -> bool:
        """A posted bill's figures are history; only notes stay editable."""
        return obj.status != PurchaseStatus.DRAFT

    def _business(self):
        from apps.accounts.services import get_business

        return get_business(self.context["request"].user)

    def validate_supplier(self, value):
        business = self._business()
        if value.business_id != business.id:
            raise serializers.ValidationError("That supplier does not exist in your business.")
        if value.is_walk_in:
            raise serializers.ValidationError(
                "A walk-in has no bill. Choose the actual supplier instead."
            )
        return value

    def validate(self, attrs):
        instance = self.instance

        # The books lock (2.0.4) applies to saving a draft, not only to posting.
        # A bill dated into a filed period is a mistake at the moment of entry,
        # not at the moment of posting, and catching it early is the whole point
        # of the rule.
        bill_date = attrs.get("bill_date") or getattr(instance, "bill_date", None)
        if bill_date:
            try:
                assert_period_open(self._business(), bill_date)
            except IssueError as exc:
                # Re-raised as a field error so DRF renders a 400 the frontend
                # can show against the date input, instead of a 500.
                raise serializers.ValidationError(
                    {exc.field or "bill_date": [exc.message]}
                ) from exc

        if instance is not None and not instance.is_draft:
            # Refuse edits to a posted bill's money, loudly. Silently ignoring
            # them reads as "saved" and is how a filed document gets rewritten.
            protected = [
                field
                for field in ENTERED_MONEY + ("bill_number", "bill_date", "supplier")
                if field in attrs and attrs[field] != getattr(instance, field)
            ]
            if protected:
                raise serializers.ValidationError(
                    {
                        field: (
                            "This bill is posted. Copy it to a new draft to change "
                            "it - a filed bill cannot be edited."
                        )
                        for field in protected
                    }
                )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items", [])
        purchase = Purchase(**validated_data)
        purchase.business = self._business()
        purchase.created_by = self.context["request"].user

        supplier = purchase.supplier
        snapshot_supplier(purchase, supplier)
        snapshot_business(purchase, purchase.business)

        # Origin defaults to where the goods come from; place of supply is
        # always where we are, for a purchase.
        purchase.origin_state_code = validated_data.get("origin_state_code") or (
            supplier.state_code or ""
        )
        purchase.place_of_supply = validated_data.get("place_of_supply") or (
            purchase.business.state_code or ""
        )
        purchase.supply_type = (
            "INTRA"
            if purchase.origin_state_code == purchase.place_of_supply
            else "INTER"
        )
        purchase.financial_year = financial_year(purchase.bill_date)

        self._guard_duplicate(purchase)
        purchase.save()

        replace_lines(purchase, items, business=purchase.business)
        recalculate_draft(purchase, business=purchase.business)
        return purchase

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)

        if "supplier" in validated_data:
            snapshot_supplier(instance, instance.supplier)
        if "bill_date" in validated_data:
            instance.financial_year = financial_year(instance.bill_date)

        self._guard_duplicate(instance)
        instance.save()

        if items is not None:
            replace_lines(instance, items, business=instance.business)
            recalculate_draft(instance, business=instance.business)
        return instance

    def _guard_duplicate(self, purchase: Purchase) -> None:
        """
        Duplicate check before saving, so the user gets a sentence rather than a
        constraint name. The database constraint still backs this up - a Python
        check alone loses to a race, and the same bill getting input credit twice
        is exactly the mistake this exists to prevent.
        """
        if not purchase.bill_number:
            return
        clash = (
            Purchase.objects.filter(
                supplier_id=purchase.supplier_id,
                bill_number=purchase.bill_number,
                financial_year=purchase.financial_year,
            )
            .exclude(pk=purchase.pk)
            .exists()
        )
        if clash:
            raise serializers.ValidationError(
                {
                    "bill_number": (
                        f"Supplier bill {purchase.bill_number} is already recorded for "
                        f"{purchase.financial_year}. Every bill from the same supplier "
                        "must have its own number."
                    )
                }
            )