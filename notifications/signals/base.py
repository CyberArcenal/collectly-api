import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from notifications.models.notification import Notification
from notifications.models.notification_log import NotificationLog
from notifications.state_transitions import NotificationStateTransitionService, NotificationLogStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# NOTIFICATION SIGNALS
# ============================================================

@receiver(pre_save, sender=Notification)
def notification_pre_save(sender, instance, **kwargs):
    """Log before saving a notification."""
    try:
        logger.info(f"[NotificationSignal] before_save: id={instance.id}, title={instance.title}, type={instance.type}")
    except Exception as e:
        logger.error(f"[NotificationSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=Notification)
def notification_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for Notification."""
    try:
        logger.info(f"[NotificationSignal] after_save: id={instance.id}, title={instance.title}, created={created}")
        
        service = NotificationStateTransitionService()
        
        if created:
            service.on_created(instance, "system")
    except Exception as e:
        logger.error(f"[NotificationSignal] after_save error: {e}")
        raise


# ============================================================
# NOTIFICATION LOG SIGNALS
# ============================================================

@receiver(pre_save, sender=NotificationLog)
def notification_log_pre_save(sender, instance, **kwargs):
    """Log before saving a notification log."""
    try:
        logger.info(f"[NotificationLogSignal] before_save: id={instance.id}, recipient_email={instance.recipient_email}, status={instance.status}")
    except Exception as e:
        logger.error(f"[NotificationLogSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=NotificationLog)
def notification_log_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = NotificationLog.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_retry_count = old.retry_count
        except NotificationLog.DoesNotExist:
            instance._old_status = None
            instance._old_retry_count = 0
    else:
        instance._old_status = None
        instance._old_retry_count = 0


@receiver(post_save, sender=NotificationLog)
def notification_log_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for NotificationLog."""
    try:
        logger.info(f"[NotificationLogSignal] after_save: id={instance.id}, recipient_email={instance.recipient_email}, status={instance.status}, created={created}")
        
        service = NotificationLogStateTransitionService()
        
        if created:
            service.on_create(instance, "system")
        else:
            # Check if status changed
            old_status = getattr(instance, '_old_status', None)
            if old_status and old_status != instance.status:
                if instance.status == NotificationLog.Status.FAILED and instance.retry_count < 3:
                    service.on_retry(instance, "system")
                elif instance.status == NotificationLog.Status.SENT:
                    service.on_acknowledge(instance, "system")
    except Exception as e:
        logger.error(f"[NotificationLogSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=NotificationLog)
def notification_log_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for NotificationLog."""
    try:
        logger.info(f"[NotificationLogSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[NotificationLogSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=NotificationLog)
def notification_log_post_delete(sender, instance, **kwargs):
    """Handle after delete events for NotificationLog."""
    try:
        logger.info(f"[NotificationLogSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[NotificationLogSignal] after_delete error: {e}")
        raise