from django.db import models
from core.models.baseModel import BaseModel
from borrowers.models.borrower import Borrower
from .debtor_group import DebtorGroup


class DebtorGroupMember(BaseModel):
    """
    Membership record linking a borrower to a group.
    """
    
    group = models.ForeignKey(
        DebtorGroup,
        on_delete=models.CASCADE,
        related_name='members',
        help_text="Group that the borrower belongs to"
    )
    debtor = models.ForeignKey(
        Borrower,
        on_delete=models.CASCADE,
        related_name='group_memberships',
        help_text="Borrower who is a member of the group"
    )
    assigned_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the borrower was assigned to the group"
    )
    
    class Meta:
        db_table = 'debtor_group_members'
        ordering = ['-assigned_at']
        unique_together = [['group', 'debtor']]  # Prevent duplicate memberships
        indexes = [
            models.Index(fields=['group', 'debtor']),
            models.Index(fields=['debtor']),
            models.Index(fields=['group']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Debtor Group Member"
        verbose_name_plural = "Debtor Group Members"

    def __str__(self):
        return f"{self.debtor.name} ∈ {self.group.name}"

    @property
    def is_active(self):
        """Check if this membership is active (not soft-deleted)."""
        return self.deleted_at is None

    def save(self, *args, **kwargs):
        """Ensure unique membership before saving."""
        # Check if membership already exists (soft-deleted or active)
        existing = DebtorGroupMember.objects.filter(
            group=self.group,
            debtor=self.debtor
        ).exclude(id=self.id).first()
        
        if existing:
            # If soft-deleted, restore it instead of creating new
            if existing.deleted_at:
                existing.deleted_at = None
                existing.save()
                # Set self to existing to avoid duplicate
                self.id = existing.id
                self._state.adding = False
            else:
                raise ValueError(
                    f"Borrower '{self.debtor.name}' is already a member of group '{self.group.name}'"
                )
        
        super().save(*args, **kwargs)