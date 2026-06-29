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


class SecurityLog(models.Model):
    EVENT_TYPES = [
        ("login", "Login"),
        ("logout", "Logout"),
        ("password_change", "Password Change"),
        ("2fa_enabled", "2FA Enabled"),
        ("2fa_disabled", "2FA Disabled"),
        ("failed_login", "Failed Login"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="security_logs"
    )

    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    details = models.TextField(blank=True, null=True)

    def delete(self, using=None, keep_parents=False):
        """Soft delete instead of hard delete"""
        self.is_deleted = True
        self.save()

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        self.updated_at = now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.event_type} @ {self.created_at}"
