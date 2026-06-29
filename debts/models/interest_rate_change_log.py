from decimal import Decimal
from django.db import models

from core.models.baseModel import BaseModel
from users.models.User import User
from .debt import Debt


class InterestRateChangeLog(BaseModel):
    """
    Log of interest rate changes.
    Tracks changes to system-wide default rates and per-loan rates.
    """
    
    setting_key = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Which rate was changed (e.g., 'default_interest_rate' or 'loan_123')"
    )
    old_value = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Previous rate value"
    )
    new_value = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="New rate value"
    )
    
    # Metadata
    changed_by = models.CharField(
        max_length=255,
        default='system',
        help_text="User who changed the rate"
    )
    reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for the change"
    )
    changed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the change was made"
    )
    
    # Optional: reference to specific loan
    loan = models.ForeignKey(
        Debt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interest_rate_changes',
        help_text="Specific loan this change applies to (null = system-wide)"
    )
    
    class Meta:
        db_table = 'interest_rate_change_logs'
        ordering = ['-changed_at']
        indexes = [
            models.Index(fields=['setting_key']),
            models.Index(fields=['changed_by']),
            models.Index(fields=['changed_at']),
            models.Index(fields=['loan']),
        ]
        verbose_name = "Interest Rate Change Log"
        verbose_name_plural = "Interest Rate Change Logs"

    def __str__(self):
        return f"Rate change #{self.id} - {self.setting_key}: {self.old_value} → {self.new_value}"

    @property
    def is_system_change(self):
        """Check if this is a system-wide rate change."""
        return self.loan is None

    @property
    def is_loan_change(self):
        """Check if this is a per-loan rate change."""
        return self.loan is not None

    @property
    def change_direction(self):
        """Return 'increase' or 'decrease' based on value change."""
        if self.old_value is None or self.new_value is None:
            return None
        if self.new_value > self.old_value:
            return 'increase'
        elif self.new_value < self.old_value:
            return 'decrease'
        return 'unchanged'

    def save(self, *args, **kwargs):
        """Auto-set setting_key based on loan reference."""
        if self.loan and not self.setting_key.startswith('loan_'):
            self.setting_key = f"loan_{self.loan.id}"
        super().save(*args, **kwargs)