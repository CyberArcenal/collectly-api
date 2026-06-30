import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from borrowers.models.borrower import Borrower
from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.state_transitions.borrower import BorrowerStateTransitionService
from borrowers.state_transitions.credit_check import CreditCheckStateTransitionService


logger = logging.getLogger(__name__)


# ============================================================
# BORROWER SIGNALS
# ============================================================

@receiver(pre_save, sender=Borrower)
def borrower_pre_save(sender, instance, **kwargs):
    """Log before saving a borrower."""
    try:
        logger.info(f"[BorrowerSignal] before_save: id={instance.id}, name={instance.name}, email={instance.email}")
    except Exception as e:
        logger.error(f"[BorrowerSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=Borrower)
def borrower_post_save(sender, instance, created, **kwargs):
    """
    Handle post-save events for Borrower.
    - On create: call on_activate
    - On update: call on_after_update
    """
    try:
        logger.info(f"[BorrowerSignal] after_save: id={instance.id}, name={instance.name}, created={created}")
        
        service = BorrowerStateTransitionService()
        
        if created:
            # New borrower created - activate
            service.on_activate(instance, "system")
        else:
            # Existing borrower updated - check for changes
            # Note: We don't have old state in post_save, so we handle this in pre_save with a flag
            # or we can skip on_after_update and handle specific field changes in a separate signal
            pass
    except Exception as e:
        logger.error(f"[BorrowerSignal] after_save error: {e}")
        raise


@receiver(pre_save, sender=Borrower)
def borrower_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = Borrower.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_name = old.name
            instance._old_email = old.email
        except Borrower.DoesNotExist:
            instance._old_status = None
            instance._old_name = None
            instance._old_email = None
    else:
        instance._old_status = None
        instance._old_name = None
        instance._old_email = None


@receiver(pre_delete, sender=Borrower)
def borrower_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for Borrower."""
    try:
        logger.info(f"[BorrowerSignal] before_delete: id={instance.id}, name={instance.name}")
        service = BorrowerStateTransitionService()
        service.on_before_delete(instance, "system")
    except Exception as e:
        logger.error(f"[BorrowerSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=Borrower)
def borrower_post_delete(sender, instance, **kwargs):
    """Handle after delete events for Borrower."""
    try:
        logger.info(f"[BorrowerSignal] after_delete: id={instance.id}")
        service = BorrowerStateTransitionService()
        service.on_deactivate({"id": instance.id}, "system")
    except Exception as e:
        logger.error(f"[BorrowerSignal] after_delete error: {e}")
        raise


# ============================================================
# CREDIT CHECK LOG SIGNALS
# ============================================================

@receiver(pre_save, sender=CreditCheckLog)
def credit_check_pre_save(sender, instance, **kwargs):
    """Log before saving a credit check log."""
    try:
        logger.info(f"[CreditCheckLogSignal] before_save: id={instance.id}, debtor_id={instance.debtor_id}, score={instance.score}")
    except Exception as e:
        logger.error(f"[CreditCheckLogSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=CreditCheckLog)
def credit_check_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for CreditCheckLog."""
    try:
        logger.info(f"[CreditCheckLogSignal] after_save: id={instance.id}, debtor_id={instance.debtor_id}, score={instance.score}, created={created}")
        
        service = CreditCheckStateTransitionService()
        
        if created:
            # New credit check performed
            service.on_check_performed(instance, "system")
    except Exception as e:
        logger.error(f"[CreditCheckLogSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=CreditCheckLog)
def credit_check_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for CreditCheckLog."""
    try:
        logger.info(f"[CreditCheckLogSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[CreditCheckLogSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=CreditCheckLog)
def credit_check_post_delete(sender, instance, **kwargs):
    """Handle after delete events for CreditCheckLog."""
    try:
        logger.info(f"[CreditCheckLogSignal] after_delete: id={instance.id}")
        service = CreditCheckStateTransitionService()
        service.on_log_deleted({"id": instance.id}, "system")
    except Exception as e:
        logger.error(f"[CreditCheckLogSignal] after_delete error: {e}")
        raise