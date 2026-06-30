import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from groups.state_transitions import DebtorGroupStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# DEBTOR GROUP SIGNALS
# ============================================================

@receiver(pre_save, sender=DebtorGroup)
def debtor_group_pre_save(sender, instance, **kwargs):
    """Log before saving a debtor group."""
    try:
        logger.info(f"[DebtorGroupSignal] before_save: id={instance.id}, name={instance.name}")
    except Exception as e:
        logger.error(f"[DebtorGroupSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=DebtorGroup)
def debtor_group_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for DebtorGroup."""
    try:
        logger.info(f"[DebtorGroupSignal] after_save: id={instance.id}, name={instance.name}, created={created}")
        
        service = DebtorGroupStateTransitionService()
        
        if created:
            service.on_created(instance, "system")
    except Exception as e:
        logger.error(f"[DebtorGroupSignal] after_save error: {e}")
        raise


@receiver(pre_save, sender=DebtorGroup)
def debtor_group_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = DebtorGroup.objects.get(pk=instance.pk)
            instance._old_name = old.name
            instance._old_color = old.color
        except DebtorGroup.DoesNotExist:
            instance._old_name = None
            instance._old_color = None
    else:
        instance._old_name = None
        instance._old_color = None


@receiver(pre_delete, sender=DebtorGroup)
def debtor_group_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for DebtorGroup."""
    try:
        logger.info(f"[DebtorGroupSignal] before_delete: id={instance.id}, name={instance.name}")
        service = DebtorGroupStateTransitionService()
        service.on_before_delete(instance, "system")
    except Exception as e:
        logger.error(f"[DebtorGroupSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=DebtorGroup)
def debtor_group_post_delete(sender, instance, **kwargs):
    """Handle after delete events for DebtorGroup."""
    try:
        logger.info(f"[DebtorGroupSignal] after_delete: id={instance.id}")
        service = DebtorGroupStateTransitionService()
        service.on_after_delete({"id": instance.id}, "system")
    except Exception as e:
        logger.error(f"[DebtorGroupSignal] after_delete error: {e}")
        raise


# ============================================================
# DEBTOR GROUP MEMBER SIGNALS
# ============================================================

@receiver(pre_save, sender=DebtorGroupMember)
def debtor_group_member_pre_save(sender, instance, **kwargs):
    """Log before saving a debtor group member."""
    try:
        logger.info(f"[DebtorGroupMemberSignal] before_save: id={instance.id}, group_id={instance.group_id}, debtor_id={instance.debtor_id}")
    except Exception as e:
        logger.error(f"[DebtorGroupMemberSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=DebtorGroupMember)
def debtor_group_member_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for DebtorGroupMember."""
    try:
        logger.info(f"[DebtorGroupMemberSignal] after_save: id={instance.id}, group_id={instance.group_id}, debtor_id={instance.debtor_id}, created={created}")
        # No state transition service called in Node.js for this
    except Exception as e:
        logger.error(f"[DebtorGroupMemberSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=DebtorGroupMember)
def debtor_group_member_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for DebtorGroupMember."""
    try:
        logger.info(f"[DebtorGroupMemberSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[DebtorGroupMemberSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=DebtorGroupMember)
def debtor_group_member_post_delete(sender, instance, **kwargs):
    """Handle after delete events for DebtorGroupMember."""
    try:
        logger.info(f"[DebtorGroupMemberSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[DebtorGroupMemberSignal] after_delete error: {e}")
        raise