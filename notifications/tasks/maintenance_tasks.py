# notifications/tasks/maintenance_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from notifications.models.notification_log import NotificationLog
from notifications.services.notification import NotificationService
from system_settings.utils import email_enabled

logger = logging.getLogger(__name__)


@shared_task
def retry_failed_notifications():
    """Periodic task to retry failed notifications."""
    logger.info("[Task] Retrying failed notifications...")
    failed_logs = NotificationLog.objects.filter(
        status=NotificationLog.Status.FAILED,
        retry_count__lt=3,
    )
    count = 0
    skipped = 0
    for log_entry in failed_logs:
        try:
            if not email_enabled():
                skipped += 1
                continue
            log_entry.retry_count += 1
            log_entry.status = NotificationLog.Status.QUEUED
            log_entry.error_message = None
            log_entry.save()
            from .send_tasks import send_email_task  # avoid circular import
            send_email_task.delay(
                to=log_entry.recipient_email,
                subject=log_entry.subject or "Notification",
                html=log_entry.payload or "",
                text=log_entry.payload or "",
                log_id=log_entry.id,
                is_retry=True,
            )
            count += 1
        except Exception as e:
            logger.error(f"[Task] Failed to queue retry for log #{log_entry.id}: {e}")

    if count > 0 or skipped > 0:
        NotificationService.notify_admins_and_staff(
            title='📧 Notification Retry Batch',
            message=f'Retry batch completed: {count} queued, {skipped} skipped.',
            type='info',
            metadata={'queued': count, 'skipped': skipped},
            user='system'
        )
    return {'retried': count, 'skipped': skipped}


@shared_task
def cleanup_old_notification_logs(days=90):
    """Clean up old notification logs."""
    try:
        logger.info(f"[Task] Cleaning up notification logs older than {days} days...")
        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = NotificationLog.objects.filter(
            created_at__lt=cutoff_date
        ).delete()
        result = {
            'deleted': deleted_count,
            'message': f'Deleted {deleted_count} notification logs older than {days} days'
        }
        logger.info(f"[Task] {result['message']}")
        return result
    except Exception as e:
        logger.error(f"[Task] Failed to cleanup notification logs: {e}")
        return {'deleted': 0, 'message': f'Cleanup failed: {str(e)}'}