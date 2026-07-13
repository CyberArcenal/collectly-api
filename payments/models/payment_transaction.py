from django.db import models
from core.models.baseModel import BaseModel
from debts.models.debt import Debt
from payment_methods.models.payment_method import PaymentMethod
from users.models.User import User


class PaymentTransaction(BaseModel):
    """
    Payment transaction record for a debt.
    Tracks all payments made against a debt.
    """
    
    debt = models.ForeignKey(
        Debt,
        on_delete=models.CASCADE,
        related_name='payments',
        help_text="Debt being paid"
    )
    method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
        help_text="Payment method used (Cash, Bank Transfer, GCash, etc.)"
    )
    
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount paid"
    )
    payment_date = models.DateField(
        db_index=True,
        help_text="Date when payment was made"
    )
    reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Reference number (e.g., transaction ID, check number)"
    )
    notes = models.TextField(
        null=True,
        blank=True,
        help_text="Additional notes about the payment"
    )
    recorded_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the payment was recorded in the system"
    )
    
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments_recorded',
        help_text="User who recorded the payment"
    )
    confirmed = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'payment_transactions'
        ordering = ['-payment_date']
        indexes = [
            models.Index(fields=['debt', '-payment_date']),
            models.Index(fields=['payment_date']),
            models.Index(fields=['reference']),
            models.Index(fields=['deleted_at']),
            models.Index(fields=['method']),
        ]
        verbose_name = "Payment Transaction"
        verbose_name_plural = "Payment Transactions"

    def __str__(self):
        return f"Payment #{self.id} - {self.debt.name} - {self.amount_display}"

    @property
    def amount_display(self):
        return f"₱{self.amount:,.2f}"

    @property
    def is_void(self):
        """Check if payment has been voided (soft-deleted)."""
        return self.deleted_at is not None

    @property
    def payment_method_name(self):
        """Get payment method name."""
        return self.method.name if self.method else "Unknown"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # No automatic debt updates here