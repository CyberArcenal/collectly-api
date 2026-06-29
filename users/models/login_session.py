import uuid
from django.db import models
from django.utils import timezone
from django.db import models
from core import settings

class LoginSession(models.Model):
    """Tracks user login sessions for JWT tokens"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_sessions"
    )
    device_name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    refresh_token = models.CharField(
        max_length=255, unique=True
    )  # Store refresh token jti
    access_token = models.CharField(
        max_length=255, blank=True
    )  # Store access token jti


    class Meta:
        verbose_name = "Login Session"
        verbose_name_plural = "Login Sessions"
        ordering = ["-last_used"]
        indexes = [
            models.Index(fields=["user", "last_used"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.device_name}"

    @property
    def is_valid(self):
        return self.is_active and timezone.now() < self.expires_at