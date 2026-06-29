from django.db import models
from core.models.baseModel import BaseModel


class PaymentMethod(BaseModel):
    """
    Payment method type (e.g., Cash, Bank Transfer, GCash, etc.)
    """
    
    name = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Name of payment method (e.g., 'Cash', 'Bank Transfer')"
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Description of the payment method"
    )
    icon = models.CharField(
        max_length=50,
        default='CreditCard',
        help_text="Icon name (for UI)"
    )
    is_default = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this is the default payment method"
    )
    
    class Meta:
        db_table = 'payment_methods'
        ordering = ['-is_default', 'name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_default']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Payment Method"
        verbose_name_plural = "Payment Methods"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Ensure only one default payment method exists."""
        if self.is_default:
            PaymentMethod.objects.filter(is_default=True).exclude(id=self.id).update(is_default=False)
        super().save(*args, **kwargs)