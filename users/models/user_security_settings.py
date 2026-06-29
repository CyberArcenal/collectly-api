import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.timezone import now
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.cache import cache
from django.db import models
from users.models.User import User
from django.core.exceptions import ValidationError
from core import settings


class UserSecuritySettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_settings",
    )
    two_factor_enabled = models.BooleanField(default=False)
    recovery_email = models.EmailField(blank=True, null=True)
    recovery_phone = models.CharField(max_length=20, blank=True, null=True)
    alert_on_new_device = models.BooleanField(default=True)
    alert_on_password_change = models.BooleanField(default=True)
    alert_on_failed_login = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        self.updated_at = now()
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        """Soft delete instead of hard delete"""
        self.is_deleted = True
        self.save()

    def __str__(self):
        return f"Security settings for {self.user.username}"
