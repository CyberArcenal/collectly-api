from django.db import models
from core.models.baseModel import BaseModel
from debts.models.debt import Debt


class LoanAgreement(BaseModel):
    """
    Loan agreement document for a debt.
    Stores contract details and file reference.
    """
    
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SIGNED = 'signed', 'Signed'
    
    debt = models.ForeignKey(
        Debt,
        on_delete=models.CASCADE,
        related_name='agreements',
        help_text="Debt associated with this agreement"
    )
    
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Agreement status (draft or signed)"
    )
    
    # Agreement details
    agreement_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of the agreement"
    )
    lender_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Name of the lender"
    )
    terms_text = models.TextField(
        null=True,
        blank=True,
        help_text="Full terms and conditions text"
    )
    
    # File attachment
    file = models.FileField(
        upload_to='agreements/%Y/%m/',
        null=True,
        blank=True,
        help_text="Uploaded agreement file (PDF/DOCX)"
    )
    
    # Signing details
    signed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the agreement was signed"
    )
    signed_by = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Who signed the agreement"
    )
    
    # Snapshot fields (copied from debt at signing time)
    principal_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Principal amount at signing"
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Interest rate at signing"
    )
    penalty_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Penalty rate at signing"
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Due date at signing"
    )
    purpose = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Loan purpose"
    )
    loan_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Loan start date"
    )
    anniversary_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Day of month for anniversary (1-31)"
    )
    
    class Meta:
        db_table = 'loan_agreements'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['debt', 'status']),
            models.Index(fields=['status']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Loan Agreement"
        verbose_name_plural = "Loan Agreements"

    def __str__(self):
        return f"Agreement #{self.id} - {self.debt.name} ({self.status})"

    @property
    def is_signed(self):
        return self.status == self.Status.SIGNED

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def has_file(self):
        return bool(self.file)