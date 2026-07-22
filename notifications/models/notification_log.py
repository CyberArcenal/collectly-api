from django.db import models
from core.models.baseModel import BaseModel
from django.utils import timezone

class NotificationLog(BaseModel):
    """
    Log of sent notifications (email/SMS).
    Tracks delivery status and retry attempts.
    """
    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'
    
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        RESEND = 'resend', 'Resend'
    
    recipient_email = models.EmailField(
        null=True, blank=True,
        help_text="Recipient email address"
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.EMAIL,
        db_index=True,
        help_text="Delivery channel"
    )
    
    recipient = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Email address or phone number"
    )
    
    subject = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Email subject"
    )
    payload = models.TextField(
        null=True,
        blank=True,
        help_text="Message content (HTML or text)"
    )
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
        help_text="Delivery status"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if delivery failed"
    )
    
    retry_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of retry attempts"
    )
    resend_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of manual resend attempts"
    )
    
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the notification was successfully sent"
    )
    last_error_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last error occurred"
    )
    
    class Meta:
        db_table = 'notification_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['recipient_email']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = "Notification Log"
        verbose_name_plural = "Notification Logs"

    def __str__(self):
        return f"Log #{self.id} - {self.recipient_email} ({self.status})"

    def mark_as_sent(self):
        """Mark notification as successfully sent."""
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.error_message = None
        self.save()

    def mark_as_failed(self, error_message):
        """Mark notification as failed."""
        self.status = self.Status.FAILED
        self.last_error_at = timezone.now()
        self.error_message = error_message
        self.retry_count += 1
        self.save()

    def mark_as_resend(self):
        """Mark notification as manually resent."""
        self.status = self.Status.RESEND
        self.sent_at = timezone.now()
        self.resend_count += 1
        self.save()