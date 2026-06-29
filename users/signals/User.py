from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model

from audit.utils.log import log_audit_event
from users.handlers.User import UserStatusHandler

User = get_user_model()


@receiver(post_save, sender=User)
def user_saved(sender, instance, created, **kwargs):
    """Audit log for user creation and update + trigger status handler"""
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

    # Trigger handler if status changed (old_status dapat ipasa kung available)
    if not created:
        # sa signals, wala tayong direct old_status, pero pwede mong i-cache bago save
        old_status = kwargs.get("old_status", None)
        new_status = instance.status
        if old_status and old_status != new_status:
            UserStatusHandler.handle_user_status_change(
                instance=instance,
                old_status=old_status,
                new_status=new_status,
                actor=None,  # walang request context sa signal
            )


@receiver(post_delete, sender=User)
def user_deleted(sender, instance, **kwargs):
    """Audit log for user deletion + trigger handler"""
    log_audit_event(
        request=None,
        user=instance,
        action_type="delete",
        model_name="User",
        object_id=str(instance.pk),
        changes={"status": "deleted"},
    )

    # Trigger handler for deleted status
    UserStatusHandler.handle_user_status_change(
        instance=instance,
        old_status=kwargs.get("old_status", None),
        new_status="deleted",
        actor=None,
    )