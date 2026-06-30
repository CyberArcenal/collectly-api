from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from audit.utils.log import log_audit_event
from users.state_transitions import UserStateTransitionService

User = get_user_model()


@receiver(pre_save, sender=User)
def user_pre_save(sender, instance, **kwargs):
    """Capture old status before save."""
    if instance.pk:
        try:
            old = User.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except User.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=User)
def user_post_save(sender, instance, created, **kwargs):
    """Audit log and trigger status transitions."""
    action_type = "create" if created else "update"
    changes = {
        "status": instance.status,
        "user_type": instance.user_type,
        "phone_number": instance.phone_number,
    }

    # Audit log
    log_audit_event(
        request=None,
        user=instance,
        action_type=action_type,
        model_name="User",
        object_id=str(instance.pk),
        changes=changes,
    )

    # Handle status change (only on update)
    if not created:
        old_status = getattr(instance, '_old_status', None)
        new_status = instance.status
        if old_status and old_status != new_status:
            # Delegate to state transition service
            UserStateTransitionService.on_status_change(
                user=instance,
                old_status=old_status,
                new_status=new_status,
                actor=None,  # No request context; can be passed from view
                request=None,
            )


@receiver(post_delete, sender=User)
def user_post_delete(sender, instance, **kwargs):
    """Handle deletion (already soft-deleted via status)."""
    # This is called when is_deleted is set to True? Actually the custom delete() method sets is_deleted=True.
    # We can treat it as a status change to 'deleted' if not already.
    # But the soft delete might be done via status field, not the delete method.
    # We'll keep a separate handler for the delete signal to log.
    log_audit_event(
        request=None,
        user=instance,
        action_type="delete",
        model_name="User",
        object_id=str(instance.pk),
        changes={"status": "deleted"},
    )

    # If status wasn't already 'deleted', we can trigger transition
    if instance.status != 'deleted':
        UserStateTransitionService.on_status_change(
            user=instance,
            old_status=instance.status,
            new_status='deleted',
            actor=None,
            request=None,
        )