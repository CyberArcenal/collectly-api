from django.db import models
from core.models.baseModel import BaseModel
from debts.models.debt import Debt


class Notification(BaseModel):
    """
    In-app notification for users.
    """
    
    class Type(models.TextChoices):
        ERROR = 'error', 'Error'
        INFO = 'info', 'Info'
        REMINDER = 'reminder', 'Reminder'
        OVERDUE = 'overdue', 'Overdue'
        PAYMENT_CONFIRMATION = 'payment_confirmation', 'Payment Confirmation'
    
    debt = models.ForeignKey(
        Debt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        help_text="Related debt (if any)"
    )
    
    title = models.CharField(
        max_length=255,
        help_text="Notification title"
    )
    message = models.TextField(
        help_text="Notification message"
    )
    type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.REMINDER,
        help_text="Notification type"
    )
    
    is_read = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether the notification has been read"
    )
    scheduled_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the notification should be sent"
    )
    
    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_read']),
            models.Index(fields=['scheduled_for']),
            models.Index(fields=['debt', 'is_read']),
            models.Index(fields=['type']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self):
        return self.title

    def mark_as_read(self):
        """Mark notification as read."""
        self.is_read = True
        self.save(update_fields=['is_read', 'updated_at'])

    def mark_as_unread(self):
        """Mark notification as unread."""
        self.is_read = False
        self.save(update_fields=['is_read', 'updated_at'])