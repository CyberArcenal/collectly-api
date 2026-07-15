# sync/models/sync_queue.py
from django.db import models
from django.utils import timezone
from core.models.baseModel import BaseModel


class SyncQueue(BaseModel):
    """
    Queue for sync items that need retry.
    """
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
    
    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'
    
    entity = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Entity name (e.g., 'Borrower', 'Debt')"
    )
    entity_id = models.PositiveIntegerField(
        help_text="ID of the record"
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        help_text="Action to perform"
    )
    data = models.JSONField(
        null=True,
        blank=True,
        help_text="Record data for create/update actions"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Queue status"
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of retry attempts"
    )
    max_retries = models.PositiveIntegerField(
        default=5,
        help_text="Maximum retry attempts"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Last error message"
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this item was processed"
    )
    
    class Meta:
        db_table = 'sync_queue'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['entity', 'entity_id'], name='idx_sync_queue_entity'),
            models.Index(fields=['status'], name='idx_sync_queue_status'),
            models.Index(fields=['created_at'], name='idx_sync_queue_created'),
            models.Index(fields=['deleted_at'], name='idx_sync_queue_deleted'),
        ]
        verbose_name = "Sync Queue"
        verbose_name_plural = "Sync Queue"
    
    def __str__(self):
        return f"{self.get_action_display()} {self.entity}#{self.entity_id} - {self.get_status_display()}"
    
    def mark_processing(self):
        """Mark item as being processed."""
        self.status = self.Status.PROCESSING
        self.save(update_fields=['status', 'updated_at'])
    
    def mark_completed(self):
        """Mark item as completed."""
        self.status = self.Status.COMPLETED
        self.processed_at = timezone.now()
        self.error_message = None
        self.save(update_fields=['status', 'processed_at', 'error_message', 'updated_at'])
    
    def mark_failed(self, error_message):
        """Mark item as failed (increment retry count)."""
        self.retry_count += 1
        self.error_message = error_message
        
        if self.retry_count >= self.max_retries:
            self.status = self.Status.FAILED
        else:
            self.status = self.Status.PENDING
        
        self.processed_at = timezone.now()
        self.save(update_fields=['status', 'retry_count', 'error_message', 'processed_at', 'updated_at'])
    
    def reset_for_retry(self):
        """Reset a failed item for retry."""
        if self.status != self.Status.FAILED:
            raise ValueError(f"Item {self.id} is not in failed status")
        
        self.status = self.Status.PENDING
        self.error_message = None
        self.save(update_fields=['status', 'error_message', 'updated_at'])
    
    def can_retry(self):
        """Check if item can be retried."""
        return self.retry_count < self.max_retries
    
    @property
    def is_pending(self):
        """Check if item is pending or failed (but retryable)."""
        return self.status in [self.Status.PENDING, self.Status.FAILED] and self.can_retry()