from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"  # full import path, since the app lives inside apps/
    label = "accounts"      # short label used in AUTH_USER_MODEL = "accounts.User"