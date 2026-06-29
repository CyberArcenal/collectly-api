from django.db import models
from core.models.baseModel import BaseModel


class DebtorGroup(BaseModel):
    """
    Group/Category for organizing borrowers.
    Used for segmentation (e.g., VIP, High-Risk, Corporate, etc.)
    """
    
    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Name of the group (e.g., 'VIP', 'High-Risk')"
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Description of the group"
    )
    color = models.CharField(
        max_length=7,
        default='#3b82f6',
        help_text="Hex color code for the group (e.g., '#3b82f6')"
    )
    
    class Meta:
        db_table = 'debtor_groups'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Debtor Group"
        verbose_name_plural = "Debtor Groups"

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        """Get total number of members in this group."""
        return self.members.filter(
            debtor__deleted_at__isnull=True,
            deleted_at__isnull=True
        ).count()

    @property
    def total_debt(self):
        """Get total outstanding debt of all members in this group."""
        total = 0
        for member in self.members.filter(deleted_at__isnull=True):
            if member.debtor and not member.debtor.deleted_at:
                total += member.debtor.total_debt
        return total

    @property
    def active_members(self):
        """Get all active members (not soft-deleted)."""
        return self.members.filter(
            debtor__deleted_at__isnull=True,
            deleted_at__isnull=True
        )