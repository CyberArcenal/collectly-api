# sync/models/task_progress.py
from django.db import models


class TaskProgress(models.Model):
    """
    Tracks progress of a background sync task.
    """
    STATUS_CHOICES = (
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    )

    task_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique task identifier (UUID)"
    )
    entity = models.CharField(
        max_length=100,
        help_text="Entity being synced (e.g., 'Borrower')"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='queued',
        db_index=True,
        help_text="Current status of the task"
    )
    total = models.IntegerField(
        default=0,
        help_text="Total number of records to process"
    )
    processed = models.IntegerField(
        default=0,
        help_text="Number of records processed so far"
    )
    failed = models.IntegerField(
        default=0,
        help_text="Number of records that failed"
    )
    current_entity = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Optional sub‑entity currently being processed"
    )
    result = models.JSONField(
        default=dict,
        blank=True,
        help_text="Final results (created, updated, conflicts, etc.)"
    )
    error = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if the task failed"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the task was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the task was last updated"
    )

    class Meta:
        db_table = 'sync_task_progress'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['task_id']),
            models.Index(fields=['status']),
            models.Index(fields=['entity']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = "Task Progress"
        verbose_name_plural = "Task Progresses"

    def __str__(self):
        return f"{self.task_id} – {self.entity} ({self.status})"