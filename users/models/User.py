from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.timezone import now
from django.core.exceptions import ValidationError

from users.enums.base import UserRole, UserStatus


def user_avatar_path(instance, filename):
    return f"avatars/user_{instance.id}/{filename}"





class User(AbstractUser):
    USER_TYPES = (
        (UserRole.VIEWER, "Viewer"),
        (UserRole.CUSTOMER, "Customer"),
        (UserRole.STAFF, "Staff"),
        (UserRole.COLLECTOR, "Collector"), 
        (UserRole.MANAGER, "Manager"),
        (UserRole.ADMIN, "Admin"),
    )

    STATUS_CHOICES = (
        (UserStatus.ACTIVE, "Active"),
        (UserStatus.RESTRICTED, "Restricted"),
        (UserStatus.SUSPENDED, "Suspended"),
        (UserStatus.DELETED, "Deleted"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=UserStatus.ACTIVE,
        help_text="Account status",
    )
    user_type = models.CharField(
        max_length=20,
        choices=USER_TYPES,
        default=UserRole.STAFF,
        help_text="Role type",
    )
    avatar = models.ImageField(upload_to="avatar/", null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)

    def delete(self, using=None, keep_parents=False):
        """Soft delete instead of hard delete"""
        self.is_deleted = True
        self.save()

    def save(self, *args, **kwargs):
        self.updated_at = now()
        if self.status not in dict(self.STATUS_CHOICES):
            raise ValidationError({"status": "Invalid status."})
        if self.user_type and self.user_type not in dict(self.USER_TYPES):
            raise ValidationError({"user_type": "Invalid user type."})
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_restricted(self) -> bool:
        return self.status == UserStatus.RESTRICTED

    @property
    def is_suspended(self) -> bool:
        return self.status == UserStatus.SUSPENDED

    @property
    def is_admin(self) -> bool:
        return self.user_type == UserRole.ADMIN

    @property
    def is_manager(self) -> bool:
        return self.user_type == UserRole.MANAGER
