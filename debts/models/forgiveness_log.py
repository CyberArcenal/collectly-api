from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator

from core.models.baseModel import BaseModel
from borrowers.models.borrower import Borrower
from users.models.User import User
from .debt import Debt


class ForgivenessLog(BaseModel):
    """
    Log of debt forgiveness transactions.
    Tracks when and how much debt was forgiven.
    """
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
    
    # Relationships
    debt = models.ForeignKey(
        Debt,
        on_delete=models.CASCADE,
        related_name='forgiveness_logs',
        help_text="Debt being forgiven"
    )
    borrower = models.ForeignKey(
        Borrower,
        on_delete=models.CASCADE,
        related_name='forgiveness_logs',
        help_text="Borrower who owns the debt (denormalized for faster queries)"
    )
    
    # Amounts
    amount_forgiven = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text="Amount forgiven"
    )
    previous_total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total amount before forgiveness"
    )
    new_total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total amount after forgiveness"
    )
    
    # Metadata
    reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for forgiveness"
    )
    created_by = models.CharField(
        max_length=255,
        help_text="User who created the forgiveness request"
    )
    
    # Approval workflow
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.APPROVED,
        help_text="Approval status"
    )
    approved_by = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="User who approved the forgiveness"
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the forgiveness was approved"
    )
    
    class Meta:
        db_table = 'forgiveness_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['borrower']),
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['debt', 'status']),
        ]
        verbose_name = "Forgiveness Log"
        verbose_name_plural = "Forgiveness Logs"

    def __str__(self):
        return f"Forgiveness #{self.id} - {self.debt.name} - ₱{self.amount_forgiven:,.2f}"

    @property
    def amount_display(self):
        return f"₱{self.amount_forgiven:,.2f}"

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING