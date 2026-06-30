import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver
from decimal import Decimal

from debts.models.debt import Debt
from debts.models.forgiveness_log import ForgivenessLog
from debts.models.interest_rate_change_log import InterestRateChangeLog
from debts.state_transitions import DebtStateTransitionService
from debts.state_transitions.interest_rate_change import InterestRateChangeLogStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# DEBT SIGNALS
# ============================================================

@receiver(pre_save, sender=Debt)
def debt_pre_save(sender, instance, **kwargs):
    """Log before saving a debt."""
    try:
        logger.info(f"[DebtSignal] before_save: id={instance.id}, name={instance.name}, totalAmount={instance.total_amount}, borrowerId={instance.borrower_id}")
    except Exception as e:
        logger.error(f"[DebtSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=Debt)
def debt_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = Debt.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_total_amount = old.total_amount
            instance._old_remaining_amount = old.remaining_amount
        except Debt.DoesNotExist:
            instance._old_status = None
            instance._old_total_amount = None
            instance._old_remaining_amount = None
    else:
        instance._old_status = None
        instance._old_total_amount = None
        instance._old_remaining_amount = None


@receiver(post_save, sender=Debt)
def debt_post_save(sender, instance, created, **kwargs):
    """
    Handle post-save events for Debt.
    - On status change: trigger appropriate transition
    - On amount reduction: trigger forgiveness
    """
    try:
        logger.info(f"[DebtSignal] after_save: id={instance.id}, name={instance.name}, status={instance.status}, created={created}")
        
        service = DebtStateTransitionService()
        
        if not created:
            # Check if status changed
            old_status = getattr(instance, '_old_status', None)
            if old_status and old_status != instance.status:
                # Status changed - trigger appropriate transition
                if instance.status == Debt.Status.PAID:
                    service.on_paid(instance, "system")
                elif instance.status == Debt.Status.OVERDUE:
                    service.on_overdue(instance, "system")
                elif instance.status == Debt.Status.DEFAULTED:
                    service.on_defaulted(instance, "system")
                elif instance.status == Debt.Status.ACTIVE and old_status != Debt.Status.ACTIVE:
                    service.on_restore_to_active(instance, "system")
            
            # Check if total amount was reduced (forgiveness)
            old_total = getattr(instance, '_old_total_amount', None)
            if old_total and old_total > instance.total_amount:
                amount_forgiven = old_total - instance.total_amount
                service.on_forgiveness(instance, amount_forgiven, "system")
        
        # Note: onCreate is not called in the original Node.js code for Debt
        # so we skip it here
        
    except Exception as e:
        logger.error(f"[DebtSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=Debt)
def debt_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for Debt."""
    try:
        logger.info(f"[DebtSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[DebtSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=Debt)
def debt_post_delete(sender, instance, **kwargs):
    """Handle after delete events for Debt."""
    try:
        logger.info(f"[DebtSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[DebtSignal] after_delete error: {e}")
        raise


# ============================================================
# FORGIVENESS LOG SIGNALS
# ============================================================

@receiver(pre_save, sender=ForgivenessLog)
def forgiveness_log_pre_save(sender, instance, **kwargs):
    """Log before saving a forgiveness log."""
    try:
        logger.info(f"[ForgivenessLogSignal] before_save: id={instance.id}, debt_id={instance.debt_id}, amount={instance.amount_forgiven}")
    except Exception as e:
        logger.error(f"[ForgivenessLogSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=ForgivenessLog)
def forgiveness_log_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for ForgivenessLog."""
    try:
        logger.info(f"[ForgivenessLogSignal] after_save: id={instance.id}, debt_id={instance.debt_id}, amount={instance.amount_forgiven}, created={created}")
        # No state transition service called in Node.js for this
    except Exception as e:
        logger.error(f"[ForgivenessLogSignal] after_save error: {e}")
        raise


# ============================================================
# INTEREST RATE CHANGE LOG SIGNALS
# ============================================================

@receiver(pre_save, sender=InterestRateChangeLog)
def interest_rate_change_pre_save(sender, instance, **kwargs):
    """Log before saving an interest rate change log."""
    try:
        logger.info(f"[InterestRateChangeLogSignal] before_save: id={instance.id}, setting_key={instance.setting_key}")
    except Exception as e:
        logger.error(f"[InterestRateChangeLogSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=InterestRateChangeLog)
def interest_rate_change_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for InterestRateChangeLog."""
    try:
        logger.info(f"[InterestRateChangeLogSignal] after_save: id={instance.id}, setting_key={instance.setting_key}, old_value={instance.old_value}, new_value={instance.new_value}, created={created}")
        
        if created:
            service = InterestRateChangeLogStateTransitionService()
            service.on_interest_rate_changed(instance, instance.changed_by or "system")
    except Exception as e:
        logger.error(f"[InterestRateChangeLogSignal] after_save error: {e}")
        raise