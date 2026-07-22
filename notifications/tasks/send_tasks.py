# notifications/tasks/send_tasks.py
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from notifications.models.notification_log import NotificationLog
from system_settings.utils import email_enabled, sms_enabled, get_smtp_config

logger = logging.getLogger(__name__)


def _update_log_status(log_id, status, error_message=None):
    """Update notification log status."""
    if not log_id:
        return
    try:
        log_entry = NotificationLog.objects.get(id=log_id)
        if log_entry.status != status:
            old_status = log_entry.status
            log_entry.status = status
            if status == NotificationLog.Status.SENT:
                log_entry.sent_at = timezone.now()
                log_entry.error_message = None
            elif status == NotificationLog.Status.FAILED:
                log_entry.last_error_at = timezone.now()
                log_entry.error_message = error_message
            elif status == NotificationLog.Status.QUEUED:
                log_entry.error_message = None
            log_entry.save()
            logger.debug(f"[Task] NotificationLog #{log_id} status updated: {old_status} → {status}")
    except NotificationLog.DoesNotExist:
        logger.warning(f"[Task] NotificationLog #{log_id} not found")
    except Exception as e:
        logger.error(f"[Task] Failed to update NotificationLog #{log_id}: {e}")


@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_email_task(self, to, subject, html, text, log_id, is_retry=False):
    """Send email asynchronously with retry logic."""
    try:
        if not email_enabled():
            logger.warning(f"[Task] Email disabled, skipping send to {to}")
            _update_log_status(log_id, NotificationLog.Status.FAILED, "Email disabled in system settings")
            return {"success": False, "error": "Email disabled"}

        logger.info(f"[Task] Sending email to {to} (log_id={log_id}, retry={is_retry})")

        smtp_config = get_smtp_config()
        if not smtp_config.get('host') or not smtp_config.get('from_email'):
            logger.warning(f"[Task] SMTP config incomplete for {to}")
            _update_log_status(log_id, NotificationLog.Status.FAILED, "SMTP configuration incomplete")
            return {"success": False, "error": "SMTP configuration incomplete"}

        from_email = f"{smtp_config.get('from_name', 'Collectly')} <{smtp_config['from_email']}>"
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text or "",
            from_email=from_email,
            to=[to],
            reply_to=[smtp_config.get('from_email')],
        )
        if html:
            msg.attach_alternative(html, "text/html")

        result = msg.send()
        _update_log_status(log_id, NotificationLog.Status.SENT)
        logger.info(f"[Task] Email sent to {to} (log_id={log_id})")
        return {"success": True, "message_id": result}

    except Exception as e:
        logger.error(f"[Task] Failed to send email to {to}: {e}")
        _update_log_status(log_id, NotificationLog.Status.FAILED, str(e))
        if self.request.retries < self.max_retries:
            retry_countdown = 2 ** self.request.retries
            logger.info(f"[Task] Retrying email to {to} in {retry_countdown}s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=retry_countdown)
        logger.error(f"[Task] All retries exhausted for email to {to}")
        return {"success": False, "error": str(e)}


@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_sms_task(self, to, message, log_id=None):
    """Send SMS asynchronously with retry logic."""
    try:
        if not sms_enabled():
            logger.warning(f"[Task] SMS disabled, skipping send to {to}")
            if log_id:
                _update_log_status(log_id, NotificationLog.Status.FAILED, "SMS disabled in system settings")
            return {"success": False, "error": "SMS disabled"}

        logger.info(f"[Task] Sending SMS to {to} (log_id={log_id})")

        from notifications.services.sms import SmsService
        sms_service = SmsService()
        if not sms_service.client:
            error_msg = "SMS service not configured (Twilio credentials missing)"
            logger.error(f"[Task] {error_msg}")
            if log_id:
                _update_log_status(log_id, NotificationLog.Status.FAILED, error_msg)
            return {"success": False, "error": error_msg}

        result = sms_service.send(to, message)
        if log_id:
            _update_log_status(log_id, NotificationLog.Status.SENT)
        logger.info(f"[Task] SMS sent to {to} (log_id={log_id})")
        return {"success": True, "sid": result.get("sid")}

    except Exception as e:
        logger.error(f"[Task] Failed to send SMS to {to}: {e}")
        if log_id:
            _update_log_status(log_id, NotificationLog.Status.FAILED, str(e))
        if self.request.retries < self.max_retries:
            retry_countdown = 2 ** self.request.retries
            logger.info(f"[Task] Retrying SMS to {to} in {retry_countdown}s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=retry_countdown)
        logger.error(f"[Task] All retries exhausted for SMS to {to}")
        return {"success": False, "error": str(e)}


@shared_task
def send_scheduled_notifications():
    """Send scheduled notifications that are due."""
    logger.info("[Task] Sending scheduled notifications...")
    now = timezone.now()
    scheduled_logs = NotificationLog.objects.filter(
        status=NotificationLog.Status.QUEUED,
        created_at__lte=now,
    )
    count = 0
    errors = 0
    for log_entry in scheduled_logs:
        try:
            if log_entry.recipient_email:
                send_email_task.delay(
                    to=log_entry.recipient_email,
                    subject=log_entry.subject or "Notification",
                    html=log_entry.payload or "",
                    text=log_entry.payload or "",
                    log_id=log_entry.id,
                )
                count += 1
            else:
                logger.warning(f"[Task] NotificationLog #{log_entry.id} has no recipient")
                errors += 1
        except Exception as e:
            logger.error(f"[Task] Failed to send scheduled notification #{log_entry.id}: {e}")
            errors += 1
    result = {
        'sent': count,
        'errors': errors,
        'message': f'Sent {count} scheduled notifications, {errors} errors'
    }
    logger.info(f"[Task] {result['message']}")
    return result