from django.db import models
from borrowers.models.borrower import Borrower
from core.models.baseModel import BaseModel
from django.utils import timezone

class LoanApplication(BaseModel):
    """
    Loan application submitted by a borrower.
    Tracks requests before they become active debts.
    """
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
    
    debtor = models.ForeignKey(
        Borrower,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='loan_applications',
        help_text="Existing borrower (if any)"
    )
    
    # Snapshot fields (in case debtor is new or deleted later)
    debtor_name = models.CharField(
        max_length=255,
        help_text="Borrower's full name (snapshot)"
    )
    debtor_contact = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Borrower's contact (snapshot)"
    )
    debtor_email = models.EmailField(
        null=True,
        blank=True,
        help_text="Borrower's email (snapshot)"
    )
    debtor_address = models.TextField(
        null=True,
        blank=True,
        help_text="Borrower's address (snapshot)"
    )
    
    # Loan details
    requested_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount requested"
    )
    purpose = models.CharField(
        max_length=255,
        help_text="Purpose of the loan"
    )
    proposed_due_date = models.DateField(
        help_text="Proposed due date"
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Proposed interest rate"
    )
    
    # Approval workflow
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        help_text="Application status"
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the application was approved"
    )
    rejected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the application was rejected"
    )
    approved_by = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Who approved the application"
    )
    rejection_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason for rejection"
    )
    
    class Meta:
        db_table = 'loan_applications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['debtor', 'status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Loan Application"
        verbose_name_plural = "Loan Applications"

    def __str__(self):
        return f"Application #{self.id} - {self.debtor_name} ({self.status})"

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def is_rejected(self):
        return self.status == self.Status.REJECTED

    @property
    def amount_display(self):
        return f"₱{self.requested_amount:,.2f}"

    def approve(self, user):
        """Approve the application."""
        self.status = self.Status.APPROVED
        self.approved_at = timezone.now()
        self.approved_by = user
        self.save()

    def reject(self, reason):
        """Reject the application."""
        self.status = self.Status.REJECTED
        self.rejected_at = timezone.now()
        self.rejection_reason = reason
        self.save()