import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from loan_applications.models.loan_application import LoanApplication
from loan_applications.state_transitions import LoanApplicationStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# LOAN APPLICATION SIGNALS
# ============================================================

@receiver(pre_save, sender=LoanApplication)
def loan_application_pre_save(sender, instance, **kwargs):
    """Log before saving a loan application."""
    try:
        logger.info(f"[LoanApplicationSignal] before_save: id={instance.id}, debtorName={instance.debtor_name}, requestedAmount={instance.requested_amount}, status={instance.status}")
    except Exception as e:
        logger.error(f"[LoanApplicationSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=LoanApplication)
def loan_application_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = LoanApplication.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_rejection_reason = old.rejection_reason
        except LoanApplication.DoesNotExist:
            instance._old_status = None
            instance._old_rejection_reason = None
    else:
        instance._old_status = None
        instance._old_rejection_reason = None


@receiver(post_save, sender=LoanApplication)
def loan_application_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for LoanApplication."""
    try:
        logger.info(f"[LoanApplicationSignal] after_save: id={instance.id}, status={instance.status}, created={created}")
        
        service = LoanApplicationStateTransitionService()
        
        if created:
            service.on_submit(instance, "system")
        else:
            # Check if status changed
            old_status = getattr(instance, '_old_status', None)
            if old_status and old_status != instance.status:
                if instance.status == LoanApplication.Status.APPROVED:
                    service.on_approve(instance, "system")
                elif instance.status == LoanApplication.Status.REJECTED:
                    service.on_reject(instance, instance.rejection_reason, "system")
                elif instance.status == LoanApplication.Status.PENDING and old_status == LoanApplication.Status.REJECTED:
                    service.on_reopen(instance, "system")
    except Exception as e:
        logger.error(f"[LoanApplicationSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=LoanApplication)
def loan_application_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for LoanApplication."""
    try:
        logger.info(f"[LoanApplicationSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[LoanApplicationSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=LoanApplication)
def loan_application_post_delete(sender, instance, **kwargs):
    """Handle after delete events for LoanApplication."""
    try:
        logger.info(f"[LoanApplicationSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[LoanApplicationSignal] after_delete error: {e}")
        raise