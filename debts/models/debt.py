from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone

from core.models.baseModel import BaseModel
from borrowers.models.borrower import Borrower


class Debt(BaseModel):
    """
    Debt/Loan model - represents a debt or loan record.
    """
    
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAID = 'paid', 'Paid'
        OVERDUE = 'overdue', 'Overdue'
        DEFAULTED = 'defaulted', 'Defaulted'
    
    class InterestPeriod(models.TextChoices):
        PER_ANNUM = 'per_annum', 'Per Annum'
        PER_MONTH = 'per_month', 'Per Month'
    
    # Relationships
    borrower = models.ForeignKey(
        Borrower,
        on_delete=models.CASCADE,
        related_name='debts',
        help_text="Borrower who owns this debt"
    )
    
    # Core fields
    name = models.CharField(
        max_length=255,
        help_text="Name/description of the debt (e.g., 'Personal Loan')"
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text="Total amount of the debt"
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total amount paid so far"
    )
    remaining_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Remaining balance (auto-calculated)"
    )
    
    # Date fields
    due_date = models.DateField(
        db_index=True,
        help_text="Due date of the debt"
    )
    last_interest_accrual_date = models.DateField(
        null=True,
        blank=True,
        help_text="Last date when interest was accrued"
    )
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        help_text="Current status of the debt"
    )
    
    # Rates
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Interest rate in percentage (e.g., 10.00 = 10%)"
    )
    penalty_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Penalty rate in percentage (e.g., 2.00 = 2%)"
    )
    interest_calculation_period = models.CharField(
        max_length=20,
        choices=InterestPeriod.choices,
        default=InterestPeriod.PER_ANNUM,
        help_text="How interest is calculated (per annum or per month)"
    )
    
    class Meta:
        db_table = 'debts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['borrower', 'status']),
            models.Index(fields=['due_date', 'status']),
            models.Index(fields=['deleted_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = "Debt"
        verbose_name_plural = "Debts"

    def __str__(self):
        return f"{self.name} - {self.borrower.name} ({self.amount_display})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    @property
    def amount_display(self):
        """Display total amount with currency symbol."""
        return f"₱{self.total_amount:,.2f}"

    @property
    def remaining_display(self):
        """Display remaining amount with currency symbol."""
        return f"₱{self.remaining_amount:,.2f}"

    @property
    def paid_percentage(self):
        """Calculate percentage paid."""
        if self.total_amount == 0:
            return 0
        return (self.paid_amount / self.total_amount) * 100

    @property
    def is_fully_paid(self):
        """Check if debt is fully paid."""
        return self.remaining_amount <= Decimal('0.01')

    @property
    def is_overdue(self):
        """Check if debt is overdue."""
        if self.status == self.Status.PAID:
            return False
        return self.due_date and self.due_date < timezone.now().date()

    @property
    def days_overdue(self):
        """Calculate days overdue."""
        if not self.is_overdue:
            return 0
        today = timezone.now().date()
        return (today - self.due_date).days

    @property
    def days_until_due(self):
        """Calculate days until due date."""
        if self.is_overdue or self.is_fully_paid:
            return 0
        today = timezone.now().date()
        return (self.due_date - today).days

    @property
    def total_payments(self):
        """Get total payments count."""
        return self.payments.filter(deleted_at__isnull=True).count()

    @property
    def total_penalties(self):
        """Get total penalties count."""
        return self.penalties.filter(deleted_at__isnull=True).count()

    @property
    def total_penalty_amount(self):
        """Get total penalty amount."""
        return sum(
            p.amount for p in self.penalties.filter(deleted_at__isnull=True)
        )