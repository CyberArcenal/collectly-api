# sync/models/sync_conflict.py
from django.db import models
from django.utils import timezone
from core.models.baseModel import BaseModel


class SyncConflict(BaseModel):
    """
    Track conflicts between local and server data.
    """
    
    class Resolution(models.TextChoices):
        PENDING = 'pending', 'Pending'
        LOCAL = 'local', 'Local (Use client data)'
        SERVER = 'server', 'Server (Use server data)'
        MANUAL = 'manual', 'Manual'
        MERGED = 'merged', 'Merged'
    
    entity = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Entity name (e.g., 'Borrower', 'Debt')"
    )
    entity_id = models.PositiveIntegerField(
        help_text="ID of the record with conflict"
    )
    local_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Local (client) version of the record"
    )
    server_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Server version of the record"
    )
    merged_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Merged version (if resolved as merged)"
    )
    resolution = models.CharField(
        max_length=20,
        choices=Resolution.choices,
        default=Resolution.PENDING,
        help_text="How the conflict was resolved"
    )
    resolved_by = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="User who resolved the conflict"
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the conflict was resolved"
    )
    local_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Local record's updated_at timestamp"
    )
    server_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Server record's updated_at timestamp"
    )
    notes = models.TextField(
        null=True,
        blank=True,
        help_text="Additional notes about the conflict"
    )
    
    class Meta:
        db_table = 'sync_conflicts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity', 'entity_id'], name='idx_sync_conflict_entity'),
            models.Index(fields=['resolution'], name='idx_sync_conflict_resolution'),
            models.Index(fields=['created_at'], name='idx_sync_conflict_created'),
            models.Index(fields=['deleted_at'], name='idx_sync_conflict_deleted'),
        ]
        verbose_name = "Sync Conflict"
        verbose_name_plural = "Sync Conflicts"
    
    def __str__(self):
        return f"{self.entity}#{self.entity_id} - {self.get_resolution_display()}"
    
    def resolve(self, resolution, resolved_by=None, merged_data=None):
        """
        Resolve the conflict.
        
        Args:
            resolution: 'local', 'server', 'manual', 'merged'
            resolved_by: Username of resolver
            merged_data: Merged data (required if resolution is 'merged')
        """
        if resolution == self.Resolution.MERGED and merged_data is None:
            raise ValueError("Merged data is required for 'merged' resolution")
        
        self.resolution = resolution
        self.resolved_by = resolved_by or 'system'
        self.resolved_at = timezone.now()
        if merged_data:
            self.merged_data = merged_data
        self.save(update_fields=['resolution', 'resolved_by', 'resolved_at', 'merged_data', 'updated_at'])
    
    def is_pending(self):
        """Check if conflict is still pending."""
        return self.resolution == self.Resolution.PENDING
    
    def is_resolved(self):
        """Check if conflict is resolved."""
        return self.resolution != self.Resolution.PENDING