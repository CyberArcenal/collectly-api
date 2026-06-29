import uuid
from django.db import models
from django.utils import timezone
from django.db import models
from core import settings


class LoginCheckpoint(models.Model):
    """Secure checkpoint for 2FA login or registration flow"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,  # allow None for pre-registration checkpoints
        blank=True
    )
    email = models.EmailField(null=True, blank=True)  # optional traceability
    token = models.CharField(max_length=255, unique=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Login Checkpoint"
        verbose_name_plural = "Login Checkpoints"
        indexes = [
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        if self.user:
            return f"Checkpoint for {self.user.email}"
        return f"Checkpoint for {self.email or 'unassigned'}"

    @property
    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at