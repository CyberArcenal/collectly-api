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


class OtpRequest(models.Model):
    """One-time password request for email verification or login"""

    EMAIL = "email"
    PHONE = "phone"
    OTP_TYPES = [
        (EMAIL, "Email"),
        (PHONE, "Phone"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="otp_requests",
        null=True,
        blank=True,
    )
    otp_code = models.CharField(max_length=6)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempt_count = models.IntegerField(default=0)
    type = models.CharField(max_length=10, choices=OTP_TYPES, default=EMAIL)
    is_email_delivered = models.BooleanField(default=False)
    is_phone_delivered = models.BooleanField(default=False)

    def clean(self):
        if not self.type in dict(self.OTP_TYPES):
            raise ValidationError({"type": "Invalid OTP type."})

        if not self.email and not self.phone:
            raise ValueError("Either email or phone must be provided.")
        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()

        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "OTP Request"

    def __str__(self):
        return f"OTP for {self.user.username} - {self.otp_code}"
