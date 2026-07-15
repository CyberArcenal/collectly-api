import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from loan_agreements.models.loan_agreement import LoanAgreement
from loan_agreements.state_transitions import LoanAgreementStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# LOAN AGREEMENT SIGNALS
# ============================================================

@receiver(pre_save, sender=LoanAgreement)
def loan_agreement_pre_save(sender, instance, **kwargs):
    """Log before saving a loan agreement."""
    try:
        logger.info(f"[LoanAgreementSignal] before_save: id={instance.id}, lenderName={instance.lender_name}, debtId={instance.debt_id}")
    except Exception as e:
        logger.error(f"[LoanAgreementSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=LoanAgreement)
def loan_agreement_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = LoanAgreement.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_signed_at = old.signed_at
            instance._old_signed_by = old.signed_by
        except LoanAgreement.DoesNotExist:
            instance._old_status = None
            instance._old_signed_at = None
            instance._old_signed_by = None
    else:
        instance._old_status = None
        instance._old_signed_at = None
        instance._old_signed_by = None


@receiver(post_save, sender=LoanAgreement)
def loan_agreement_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for LoanAgreement."""
    try:
        logger.info(f"[LoanAgreementSignal] after_save: id={instance.id}, status={instance.status}, created={created}")
        
        service = LoanAgreementStateTransitionService()
        
        if created:
            service.on_created(instance, "system")
        else:
            # Check if status changed from draft to signed
            old_status = getattr(instance, '_old_status', None)
            if old_status == "draft" and instance.status == "signed":
                service.on_signed(instance, "system")
            else:
                # For other updates
                service.on_updated(None, instance, "system")
    except Exception as e:
        logger.error(f"[LoanAgreementSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=LoanAgreement)
def loan_agreement_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for LoanAgreement."""
    try:
        logger.info(f"[LoanAgreementSignal] before_delete: id={instance.id}")
        service = LoanAgreementStateTransitionService()
        service.on_before_delete(instance, "system")
    except Exception as e:
        logger.error(f"[LoanAgreementSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=LoanAgreement)
def loan_agreement_post_delete(sender, instance, **kwargs):
    """Handle after delete events for LoanAgreement."""
    try:
        logger.info(f"[LoanAgreementSignal] after_delete: id={instance.id}")
        service = LoanAgreementStateTransitionService()
        service.on_after_delete(instance, "system")
    except Exception as e:
        logger.error(f"[LoanAgreementSignal] after_delete error: {e}")
        raise