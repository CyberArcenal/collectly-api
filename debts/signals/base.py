import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver
from decimal import Decimal
from django.utils import timezone

from debts.models.debt import Debt
from debts.models.forgiveness_log import ForgivenessLog
from debts.models.interest_rate_change_log import InterestRateChangeLog
from debts.state_transitions import DebtStateTransitionService
from debts.state_transitions.interest_rate_change import InterestRateChangeLogStateTransitionService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


# ============================================================
# DEBT SIGNALS
# ============================================================


@receiver(pre_save, sender=Debt)
def debt_pre_save_recalculate_remaining(sender, instance, **kwargs):
    """
    Automatically recalculate remaining_amount before saving if total_amount
    or paid_amount changed.
    """
    # Skip if flag is set (to avoid recursion)
    if getattr(instance, '_skip_recalc', False):
        logger.debug(f"[DebtSignal] pre_save_recalc SKIPPED (flag set) for debt #{instance.id}")
        return

    # For new instances, just compute
    if not instance.pk:
        instance.remaining_amount = instance.total_amount - instance.paid_amount
        if instance.remaining_amount < 0:
            instance.remaining_amount = Decimal('0')
        logger.info(f"[DebtSignal] pre_save_recalc NEW debt: total={instance.total_amount}, paid={instance.paid_amount}, remaining={instance.remaining_amount}")
        return

    # For existing instances, check if total_amount or paid_amount changed
    try:
        old = Debt.objects.get(pk=instance.pk)
        old_total = old.total_amount
        old_paid = old.paid_amount
        old_remaining = old.remaining_amount

        if old.total_amount != instance.total_amount or old.paid_amount != instance.paid_amount:
            instance.remaining_amount = instance.total_amount - instance.paid_amount
            if instance.remaining_amount < 0:
                instance.remaining_amount = Decimal('0')
            logger.info(
                f"[DebtSignal] pre_save_recalc debt #{instance.id}: "
                f"total {old_total}->{instance.total_amount}, "
                f"paid {old_paid}->{instance.paid_amount}, "
                f"remaining {old_remaining}->{instance.remaining_amount}"
            )
        else:
            logger.debug(f"[DebtSignal] pre_save_recalc debt #{instance.id}: no change in total or paid")
    except Debt.DoesNotExist:
        logger.warning(f"[DebtSignal] pre_save_recalc debt #{instance.id} not found in DB (maybe new?)")


@receiver(post_save, sender=Debt)
def debt_post_save_update_status_if_fully_paid(sender, instance, created, **kwargs):
    """
    After saving, if remaining_amount <= 0 and status is not PAID,
    update status and trigger on_paid transition.
    """
    # Skip if flag is set (to avoid recursion)
    if getattr(instance, '_skip_recalc', False):
        logger.debug(f"[DebtSignal] post_save_status SKIPPED (flag set) for debt #{instance.id}")
        return

    logger.info(
        f"[DebtSignal] post_save_status debt #{instance.id}: "
        f"status={instance.status}, remaining={instance.remaining_amount}, created={created}"
    )

    # If fully paid and not already marked as PAID
    if instance.remaining_amount <= Decimal('0.01') and instance.status != Debt.Status.PAID:
        old_status = instance.status
        instance.status = Debt.Status.PAID
        instance.updated_at = timezone.now()
        # Set flag to prevent re-entering this signal
        instance._skip_recalc = True
        instance.save(update_fields=['status', 'updated_at'])

        logger.info(f"[DebtSignal] debt #{instance.id} marked as PAID (was {old_status})")

        # Audit log for status change
        log_audit_event(
            request=None,
            user='system',
            action_type='debt_status_auto_paid',
            model_name='Debt',
            object_id=str(instance.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': Debt.Status.PAID},
                'reason': 'fully paid',
            }
        )

        # Trigger the on_paid transition (notifications, credit score, etc.)
        # This method should NOT update the debt again, only send notifications etc.
        DebtStateTransitionService.on_paid(instance, user='system', request=None)
    else:
        logger.debug(
            f"[DebtSignal] post_save_status debt #{instance.id}: "
            f"not fully paid (remaining={instance.remaining_amount}) or already PAID"
        )


@receiver(pre_save, sender=Debt)
def debt_pre_save(sender, instance, **kwargs):
    """Log before saving a debt."""
    try:
        # Get old values if exists
        old_paid = None
        old_remaining = None
        if instance.pk:
            try:
                old = Debt.objects.get(pk=instance.pk)
                old_paid = old.paid_amount
                old_remaining = old.remaining_amount
            except Debt.DoesNotExist:
                pass

        logger.info(
            f"[DebtSignal] before_save: id={instance.id}, name={instance.name}, "
            f"totalAmount={instance.total_amount}, paidAmount={instance.paid_amount}, "
            f"remaining={instance.remaining_amount}, old_paid={old_paid}, old_remaining={old_remaining}, "
            f"borrowerId={instance.borrower_id}"
        )
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
            # Also capture old paid_amount for debugging
            instance._old_paid_amount = old.paid_amount
        except Debt.DoesNotExist:
            instance._old_status = None
            instance._old_total_amount = None
            instance._old_remaining_amount = None
            instance._old_paid_amount = None
    else:
        instance._old_status = None
        instance._old_total_amount = None
        instance._old_remaining_amount = None
        instance._old_paid_amount = None


@receiver(post_save, sender=Debt)
def debt_post_save(sender, instance, created, **kwargs):
    """
    Handle post-save events for Debt.
    - On status change: trigger appropriate transition
    - On amount reduction: trigger forgiveness
    """
    try:
        logger.info(
            f"[DebtSignal] after_save: id={instance.id}, name={instance.name}, "
            f"status={instance.status}, paid={instance.paid_amount}, "
            f"remaining={instance.remaining_amount}, created={created}"
        )

        service = DebtStateTransitionService()

        if not created:
            # Check if status changed
            old_status = getattr(instance, '_old_status', None)
            if old_status and old_status != instance.status:
                logger.info(f"[DebtSignal] status changed: {old_status} -> {instance.status} for debt #{instance.id}")
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
                logger.info(f"[DebtSignal] forgiveness detected: {amount_forgiven} for debt #{instance.id}")
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