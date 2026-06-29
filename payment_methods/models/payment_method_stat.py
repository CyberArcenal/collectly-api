from django.db import models
from core.models.baseModel import BaseModel
from .payment_method import PaymentMethod


class PaymentMethodStat(BaseModel):
    """
    Usage statistics for payment methods.
    Aggregates transaction counts and amounts.
    """
    
    method = models.OneToOneField(
        PaymentMethod,
        on_delete=models.CASCADE,
        related_name='stats',
        help_text="Payment method these stats belong to"
    )
    
    transaction_count = models.PositiveIntegerField(
        default=0,
        help_text="Total number of transactions using this method"
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Total amount processed using this method"
    )
    
    class Meta:
        db_table = 'payment_method_stats'
        verbose_name = "Payment Method Stat"
        verbose_name_plural = "Payment Method Stats"

    def __str__(self):
        return f"{self.method.name} Stats"

    @property
    def average_transaction(self):
        """Calculate average transaction amount."""
        if self.transaction_count == 0:
            return 0
        return self.total_amount / self.transaction_count

    def increment(self, amount):
        """Increment stats by transaction."""
        self.transaction_count += 1
        self.total_amount += amount
        self.save()

    def decrement(self, amount):
        """Decrement stats (e.g., when payment is voided)."""
        self.transaction_count -= 1
        if self.transaction_count < 0:
            self.transaction_count = 0
        self.total_amount -= amount
        if self.total_amount < 0:
            self.total_amount = 0
        self.save()