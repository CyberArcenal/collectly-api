from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid

class AuditPolicy(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('draft', 'Draft'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    retention_years = models.IntegerField(default=5)
    immutable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.retention_years <= 0:
            raise ValidationError("Retention years must be positive.")

    def save(self, *args, **kwargs):
        # Enforce immutability of policy once set
        if self.pk is not None and self.immutable:
            raise ValidationError("AuditPolicy is immutable and cannot be updated.")

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"AuditPolicy: {self.retention_years} years retention"
