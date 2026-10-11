"""Admin configuration for the email-based User."""

from django.contrib import admin, messages  # pyright: ignore[reportMissingModuleSource]
from django.contrib.auth.admin import (
    UserAdmin as DjangoUserAdmin,  # pyright: ignore[reportMissingModuleSource]
)
from django.db.models.deletion import ProtectedError

from .models import BusinessProfile, User

#: Said wherever a delete is refused. One string so the API, the admin and the
#: tests cannot drift apart about what "close account" actually means.
NO_DELETE_MESSAGE = (
    "This account owns business records and cannot be deleted. Deactivate the "
    "user instead, and export the data if the account is being closed."
)


def _business_has_records(business) -> str:
    """A short, human summary of what would be destroyed. Used in the warning."""
    invoices = business.invoices_invoice_set.count()
    parties = business.parties_party_set.count()
    items = business.inventory_item_set.count()
    return f"{invoices} invoice(s), {parties} part(ies), {items} item(s)"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)

    def has_delete_permission(self, request, obj=None):
        """
        Refuse to delete a user who owns a business (2.0.10).

        The database already refuses via `PROTECT`; this exists so the admin
        says *why* instead of raising `ProtectedError` with no explanation.
        """
        if obj is None:
            return super().has_delete_permission(request, obj)
        business = getattr(obj, "business", None)
        if business is None:
            return super().has_delete_permission(request, obj)
        self.message_user(
            request,
            f"{obj.email} owns {_business_has_records(business)}. {NO_DELETE_MESSAGE}",
            level=messages.WARNING,
        )
        return False

    def delete_model(self, request, obj):
        """Belt and braces in case a caller bypasses the permission check."""
        try:
            obj.delete()
        except ProtectedError:
            self.message_user(request, NO_DELETE_MESSAGE, level=messages.ERROR)
            return
        super().delete_model(request, obj)


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ("trade_name", "user", "gstin", "state_code", "created_at")
    search_fields = ("trade_name", "company_name", "gstin", "user__email")

    def has_delete_permission(self, request, obj=None):
        """
        Never delete a business. Every tenant model inherits
        `business = PROTECT`, so the database would refuse anyway - but an admin
        delete that dies on a stack trace teaches the operator nothing.
        """
        if obj is None:
            return super().has_delete_permission(request, obj)
        self.message_user(
            request,
            f"Cannot delete {obj.trade_name}: it holds "
            f"{_business_has_records(obj)}. Deactivate the owner instead.",
            level=messages.WARNING,
        )
        return False

    def delete_model(self, request, obj):
        try:
            obj.delete()
        except ProtectedError:
            self.message_user(request, NO_DELETE_MESSAGE, level=messages.ERROR)
            return
        super().delete_model(request, obj)
