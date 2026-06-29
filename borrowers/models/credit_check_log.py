from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from core.models.baseModel import BaseModel
from users.models.User import User
from .borrower import Borrower


class CreditCheckLog(BaseModel):
    """
    Credit check history for a borrower.
    Tracks credit scores and risk levels over time.
    """
    
    class RiskLevel(models.TextChoices):
        LOW = 'Low', 'Low'
        MEDIUM = 'Medium', 'Medium'
        HIGH = 'High', 'High'
    
    debtor = models.ForeignKey(
        Borrower,
        on_delete=models.CASCADE,
        related_name='credit_checks',
        help_text="Borrower being checked"
    )
    
    score = models.PositiveSmallIntegerField(
        default=0,
        validators=[
            MinValueValidator(300, message="Score must be at least 300"),
            MaxValueValidator(850, message="Score must be at most 850")
        ],
        help_text="Credit score (300-850 range)"
    )
    risk_level = models.CharField(
        max_length=10,
        choices=RiskLevel.choices,
        help_text="Risk level based on score"
    )
    remarks = models.TextField(
        null=True,
        blank=True,
        help_text="Additional remarks about the credit check"
    )
    date_checked = models.DateTimeField(
        auto_now_add=True,
        help_text="When the credit check was performed"
    )
    
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='credit_checks_performed',
        help_text="User who performed the credit check"
    )
    
    external_reference = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Reference ID from external credit bureau"
    )
    
    class Meta:
        db_table = 'credit_check_logs'
        ordering = ['-date_checked']
        indexes = [
            models.Index(fields=['debtor', '-date_checked']),
            models.Index(fields=['risk_level']),
            models.Index(fields=['date_checked']),
        ]
        verbose_name = "Credit Check Log"
        verbose_name_plural = "Credit Check Logs"

    def __str__(self):
        return f"{self.debtor.name} - Score: {self.score} ({self.risk_level})"

    @property
    def is_passing(self):
        """Check if the score meets minimum requirements (e.g., >= 600)."""
        return self.score >= 600

    @property
    def is_excellent(self):
        """Check if score is excellent (>= 750)."""
        return self.score >= 750

    def save(self, *args, **kwargs):
        """Auto-set risk level based on score if not explicitly set."""
        if not self.risk_level:
            if self.score >= 700:
                self.risk_level = self.RiskLevel.LOW
            elif self.score >= 500:
                self.risk_level = self.RiskLevel.MEDIUM
            else:
                self.risk_level = self.RiskLevel.HIGH
        super().save(*args, **kwargs)