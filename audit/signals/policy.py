# audit/signals.py
import logging
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from audit.handlers.policy import AuditPolicyStatusHandler
from audit.models import AuditPolicy
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=AuditPolicy)
def capture_old_status(sender, instance, **kwargs):
    """Capture the old status before saving"""
    if not instance.pk:
        instance._old_status = None
        return

    try:
        old = sender.objects.get(pk=instance.pk)
        instance._old_status = old.status
    except sender.DoesNotExist:
        instance._old_status = None


@receiver(post_save, sender=AuditPolicy)
def handle_status_change(sender, instance: AuditPolicy, created, **kwargs):
    """Handle audit policy status changes with audit logging"""
    old_status = getattr(instance, "_old_status", None)
    new_status = instance.status

    if created or old_status != new_status:
        logger.info(
            f"Status change detected for AuditPolicy {instance.id}: {old_status} -> {new_status}"
        )

        # Business logic handler
        AuditPolicyStatusHandler.handle_policy_status_change(
            instance, old_status, new_status, user=getattr(instance, "proceed_by", None)
        )

        # Audit log entry
        log_audit_event(
            request=None,  # signals don't have request context
            user=getattr(instance, "proceed_by", None),
            action_type="status_change",   # <-- aligned
            model_name="AuditPolicy",      # <-- aligned
            object_id=str(instance.id),    # <-- aligned
            changes={                      # <-- aligned
                "old_status": old_status,
                "new_status": new_status,
                "policy_name": instance.name,
                "description": instance.description,
            },
            ip_address=None,               # <-- aligned
            user_agent=None,               # <-- aligned
        )



@receiver(post_save, sender=AuditPolicy)
def handle_policy_notifications(sender, instance: AuditPolicy, created, **kwargs):
    """Handle audit policy notifications"""
    if created:
        # New policy created
        # NotificationHandler.send_policy_created(instance)
        pass
    else:
        if hasattr(instance, "_old_status") and instance._old_status != instance.status:
            # NotificationHandler.send_policy_status_update(
            #     instance, instance._old_status, instance.status
            # )
            pass