"""Custom user model: log in with email instead of a username."""
from django.contrib.auth.models import AbstractUser, BaseUserManager # pyright: ignore[reportMissingModuleSource]
from django.db import models # pyright: ignore[reportMissingModuleSource]


class UserManager(BaseUserManager):
    """Creates users and superusers identified by email."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        # Lowercase so "Raj@X.com" and "raj@x.com" are the same account.
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)  # hashes the password; never stores plain text
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not (extra_fields["is_staff"] and extra_fields["is_superuser"]):
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Application user. Business details live in BusinessProfile (next step)."""

    username = None  # removed: email is the login identifier
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []  # email + password are always asked for

    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.lower()  # keep emails consistent even via the admin
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email