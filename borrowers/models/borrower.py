from django.db import models
from core.models.baseModel import BaseModel
from users.models.User import User


class Borrower(BaseModel):
    """
    Borrower/Debtor model - represents a person or entity with debts.
    Soft-deletable via BaseModel (deleted_at field).
    """
    
    name = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Full name of the borrower"
    )
    contact = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Phone number or contact details"
    )
    email = models.EmailField(
        null=True,
        blank=True,
        unique=True,
        help_text="Email address (must be unique)"
    )
    address = models.TextField(
        null=True,
        blank=True,
        help_text="Physical address"
    )
    notes = models.TextField(
        null=True,
        blank=True,
        help_text="Additional notes about the borrower"
    )
    
    # Optional: link to system user (if borrower has portal access)
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='borrower_profile',
        help_text="Associated system user account (if borrower has login access)"
    )
    
    class Meta:
        db_table = 'borrowers'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['email']),
            models.Index(fields=['contact']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Borrower"
        verbose_name_plural = "Borrowers"

    def __str__(self):
        return self.name

    @property
    def full_contact(self):
        """Return formatted contact information."""
        parts = []
        if self.name:
            parts.append(self.name)
        if self.contact:
            parts.append(f"({self.contact})")
        if self.email:
            parts.append(f"<{self.email}>")
        return " ".join(parts)

    @property
    def total_debt(self):
        """Calculate total outstanding debt for this borrower."""
        return sum(
            debt.remaining_amount 
            for debt in self.debts.filter(
                deleted_at__isnull=True,
                status__in=['active', 'overdue']
            )
        )

    @property
    def active_debt_count(self):
        """Count of active debts."""
        return self.debts.filter(
            deleted_at__isnull=True,
            status__in=['active', 'overdue']
        ).count()