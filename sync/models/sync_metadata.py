# sync/models/sync_metadata.py
from django.db import models
from django.utils import timezone
from core.models.baseModel import BaseModel


class SyncMetadata(BaseModel):
    """
    Track sync status for each entity.
    Used by both offline and online sync systems.
    """
    
    class Status(models.TextChoices):
        IDLE = 'idle', 'Idle'
        SYNCING = 'syncing', 'Syncing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
    
    entity = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Entity name (e.g., 'Borrower', 'Debt', 'PaymentTransaction')"
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful sync timestamp"
    )
    last_sync_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of records synced in last sync"
    )
    total_synced = models.PositiveIntegerField(
        default=0,
        help_text="Total records synced since beginning"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IDLE,
        help_text="Sync status"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Last error message if sync failed"
    )
    last_sync_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last sync started"
    )
    
    class Meta:
        db_table = 'sync_metadata'
        ordering = ['entity']
        indexes = [
            models.Index(fields=['entity'], name='idx_sync_meta_entity'),
            models.Index(fields=['status'], name='idx_sync_meta_status'),
            models.Index(fields=['last_synced_at'], name='idx_sync_meta_last_synced'),
            models.Index(fields=['deleted_at'], name='idx_sync_meta_deleted'),
        ]
        verbose_name = "Sync Metadata"
        verbose_name_plural = "Sync Metadata"
    
    def __str__(self):
        return f"{self.entity} - {self.get_status_display()}"
    
    def mark_syncing(self):
        """Mark entity as currently syncing."""
        self.status = self.Status.SYNCING
        self.last_sync_started_at = timezone.now()
        self.save(update_fields=['status', 'last_sync_started_at', 'updated_at'])
    
    def mark_completed(self, count=0):
        """Mark entity as successfully synced."""
        self.status = self.Status.COMPLETED
        self.last_synced_at = timezone.now()
        self.last_sync_count = count
        self.total_synced += count
        self.error_message = None
        self.save(update_fields=['status', 'last_synced_at', 'last_sync_count', 'total_synced', 'error_message', 'updated_at'])
    
    def mark_failed(self, error_message):
        """Mark entity as failed sync."""
        self.status = self.Status.FAILED
        self.error_message = error_message
        self.save(update_fields=['status', 'error_message', 'updated_at'])
    
    def reset(self):
        """Reset sync status to idle."""
        self.status = self.Status.IDLE
        self.error_message = None
        self.save(update_fields=['status', 'error_message', 'updated_at'])