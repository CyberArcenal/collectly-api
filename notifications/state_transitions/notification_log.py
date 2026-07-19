import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.models.notification_log import NotificationLog
from notifications.tasks import send_email_task
from system_settings.utils import email_enabled

logger = logging.getLogger(__name__)


class NotificationLogStateTransitionService:
    """
    Service for handling notification log state transitions.

    Handles creation, retry, and acknowledgment of notification logs.
    Manages email delivery and retry logic.
    """

    # ============================================================
    # CONSTANTS
    # ============================================================

    MAX_RETRIES = 3

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    def _queue_email_delivery(log_entry):
        """
        Queue email delivery via Celery.

        Args:
            log_entry: NotificationLog instance
        """
        try:
            # Check if email is enabled
            if not email_enabled():
                logger.warning(
                    f"[NotificationLogTransition] Email is disabled, "
                    f"skipping delivery for log #{log_entry.id}"
                )
                log_entry.status = NotificationLog.Status.FAILED
                log_entry.error_message = "Email is disabled in system settings"
                log_entry.last_error_at = timezone.now()
                log_entry.save()
                return

            # Queue email via Celery
            send_email_task.delay(
                to=log_entry.recipient_email,
                subject=log_entry.subject or "Notification",
                html=log_entry.payload or "",
                text=log_entry.payload or "",
                log_id=log_entry.id,
                is_retry=False,
            )

            logger.info(
                f"[NotificationLogTransition] Queued email delivery "
                f"for log #{log_entry.id} to {log_entry.recipient_email}"
            )

        except Exception as e:
            logger.error(
                f"[NotificationLogTransition] Failed to queue email "
                f"for log #{log_entry.id}: {e}"
            )
            log_entry.status = NotificationLog.Status.FAILED
            log_entry.error_message = str(e)
            log_entry.last_error_at = timezone.now()
            log_entry.save()

    @staticmethod
    def _send_retry_email(log_entry):
        """
        Queue a retry email delivery.

        Args:
            log_entry: NotificationLog instance
        """
        try:
            if not email_enabled():
                logger.warning(
                    f"[NotificationLogTransition] Email is disabled, "
                    f"skipping retry for log #{log_entry.id}"
                )
                return

            send_email_task.delay(
                to=log_entry.recipient_email,
                subject=log_entry.subject or "Notification",
                html=log_entry.payload or "",
                text=log_entry.payload or "",
                log_id=log_entry.id,
                is_retry=True,
            )

            logger.info(
                f"[NotificationLogTransition] Queued retry email "
                f"for log #{log_entry.id}"
            )

        except Exception as e:
            logger.error(
                f"[NotificationLogTransition] Failed to queue retry email "
                f"for log #{log_entry.id}: {e}"
            )

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_create(log_entry:NotificationLog, user="system", request=None):
        """
        Handle post-creation: queue delivery based on channel.
        """
        logger.info(
            f"[NotificationLogTransition] on_create: "
            f"log_id={log_entry.id}, recipient={log_entry.recipient}, "
            f"channel={log_entry.channel}, status={log_entry.status}"
        )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_log_create',
            model_name='NotificationLog',
            object_id=str(log_entry.id),
            changes={
                'recipient': log_entry.recipient,
                'channel': log_entry.channel,
                'subject': log_entry.subject,
                'status': log_entry.status,
            }
        )

        # Only queue if status is QUEUED
        if log_entry.status != NotificationLog.Status.QUEUED:
            logger.info(f"[NotificationLogTransition] Log #{log_entry.id} not queued, skipping delivery")
            return log_entry

        # Dispatch based on channel
        if log_entry.channel == NotificationLog.Channel.EMAIL:
            # Queue email via Celery
            from notifications.tasks import send_email_task
            send_email_task.delay(
                to=log_entry.recipient,
                subject=log_entry.subject or "Notification",
                html=log_entry.payload or "",
                text=log_entry.payload or "",
                log_id=log_entry.id,
                is_retry=False,
            )
            logger.info(f"[NotificationLogTransition] Queued email for log #{log_entry.id}")
        elif log_entry.channel == NotificationLog.Channel.SMS:
            # Queue SMS via Celery
            from notifications.tasks import send_sms_task
            send_sms_task.delay(
                to=log_entry.recipient,
                message=log_entry.payload or "",
                log_id=log_entry.id,
            )
            logger.info(f"[NotificationLogTransition] Queued SMS for log #{log_entry.id}")
        else:
            logger.warning(f"[NotificationLogTransition] Unknown channel: {log_entry.channel}")

        return log_entry

    @staticmethod
    @transaction.atomic
    def on_retry(log_entry, user="system", request=None):
        """
        Handle notification log retry events.

        Args:
            log_entry: NotificationLog instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            NotificationLog: The updated log instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[NotificationLogTransition] on_retry: "
            f"log_id={log_entry.id}, retry_count={log_entry.retry_count}, "
            f"user={user}"
        )

        # Check if max retries exceeded
        if log_entry.retry_count >= NotificationLogStateTransitionService.MAX_RETRIES:
            raise ValidationError({
                'detail': f'Max retries ({NotificationLogStateTransitionService.MAX_RETRIES}) exceeded.'
            })

        # Increment retry count
        old_retry_count = log_entry.retry_count
        log_entry.retry_count += 1
        log_entry.status = NotificationLog.Status.QUEUED
        log_entry.error_message = None
        log_entry.updated_at = timezone.now()
        log_entry.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_log_retry',
            model_name='NotificationLog',
            object_id=str(log_entry.id),
            changes={
                'before': {'retry_count': old_retry_count, 'status': 'failed'},
                'after': {'retry_count': log_entry.retry_count, 'status': 'queued'},
            }
        )

        # Queue retry email
        NotificationLogStateTransitionService._send_retry_email(log_entry)

        logger.info(f"[NotificationLogTransition] Notification log #{log_entry.id} retry queued")
        return log_entry

    @staticmethod
    @transaction.atomic
    def on_acknowledge(log_entry, user="system", request=None):
        """
        Handle successful delivery acknowledgment.

        Args:
            log_entry: NotificationLog instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            NotificationLog: The updated log instance
        """
        logger.info(
            f"[NotificationLogTransition] on_acknowledge: "
            f"log_id={log_entry.id}, sent_at={log_entry.sent_at}, user={user}"
        )

        # Only update if not already acknowledged
        if log_entry.status != NotificationLog.Status.SENT:
            old_status = log_entry.status
            log_entry.status = NotificationLog.Status.SENT
            log_entry.sent_at = log_entry.sent_at or timezone.now()
            log_entry.error_message = None
            log_entry.updated_at = timezone.now()
            log_entry.save()

            # Audit log
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_log_acknowledge',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={
                    'before': {'status': old_status},
                    'after': {'status': 'sent'},
                }
            )

            logger.info(f"[NotificationLogTransition] Notification log #{log_entry.id} acknowledged")
        else:
            logger.debug(f"[NotificationLogTransition] Notification log #{log_entry.id} already acknowledged")

        return log_entry

    @staticmethod
    @transaction.atomic
    def on_delivery_failed(log_entry, error_message, user="system", request=None):
        """
        Handle delivery failure.

        Args:
            log_entry: NotificationLog instance
            error_message: Error message
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            NotificationLog: The updated log instance
        """
        logger.warning(
            f"[NotificationLogTransition] on_delivery_failed: "
            f"log_id={log_entry.id}, error={error_message}, user={user}"
        )

        old_status = log_entry.status
        log_entry.status = NotificationLog.Status.FAILED
        log_entry.error_message = error_message
        log_entry.last_error_at = timezone.now()
        log_entry.updated_at = timezone.now()
        log_entry.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_log_failed',
            model_name='NotificationLog',
            object_id=str(log_entry.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'failed', 'error': error_message},
            }
        )

        # Auto-retry if retry_count < max_retries
        if log_entry.retry_count < NotificationLogStateTransitionService.MAX_RETRIES:
            logger.info(
                f"[NotificationLogTransition] Auto-retrying log #{log_entry.id} "
                f"(attempt {log_entry.retry_count + 1}/{NotificationLogStateTransitionService.MAX_RETRIES})"
            )
            NotificationLogStateTransitionService.on_retry(log_entry, user, request)

        return log_entry