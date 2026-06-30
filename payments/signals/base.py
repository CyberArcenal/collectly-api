import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from payments.state_transitions import (
    PaymentTransactionStateTransitionService,
    PenaltyTransactionStateTransitionService,
)

logger = logging.getLogger(__name__)


# ============================================================
# PAYMENT TRANSACTION SIGNALS
# ============================================================

@receiver(pre_save, sender=PaymentTransaction)
def payment_transaction_pre_save(sender, instance, **kwargs):
    """Log before saving a payment transaction."""
    try:
        logger.info(f"[PaymentTransactionSignal] before_save: id={instance.id}, amount={instance.amount}, debt_id={instance.debt_id}, reference={instance.reference}")
    except Exception as e:
        logger.error(f"[PaymentTransactionSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=PaymentTransaction)
def payment_transaction_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = PaymentTransaction.objects.get(pk=instance.pk)
            instance._old_deleted_at = old.deleted_at
            instance._old_amount = old.amount
            instance._old_refund_amount = getattr(old, 'refund_amount', None)
        except PaymentTransaction.DoesNotExist:
            instance._old_deleted_at = None
            instance._old_amount = None
            instance._old_refund_amount = None
    else:
        instance._old_deleted_at = None
        instance._old_amount = None
        instance._old_refund_amount = None


@receiver(post_save, sender=PaymentTransaction)
def payment_transaction_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for PaymentTransaction."""
    try:
        logger.info(f"[PaymentTransactionSignal] after_save: id={instance.id}, amount={instance.amount}, debt_id={instance.debt_id}, created={created}")
        
        service = PaymentTransactionStateTransitionService()
        
        if created:
            service.on_confirm(instance, "system")
        else:
            # Check if voided (deleted_at just set)
            old_deleted_at = getattr(instance, '_old_deleted_at', None)
            if old_deleted_at is None and instance.deleted_at is not None:
                service.on_void(instance, "system")
            
            # Check for refund
            old_refund_amount = getattr(instance, '_old_refund_amount', 0)
            if old_refund_amount != instance.refund_amount and instance.refund_amount > 0:
                service.on_refund(instance, instance.refund_amount, "system")
    except Exception as e:
        logger.error(f"[PaymentTransactionSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=PaymentTransaction)
def payment_transaction_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for PaymentTransaction."""
    try:
        logger.info(f"[PaymentTransactionSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PaymentTransactionSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=PaymentTransaction)
def payment_transaction_post_delete(sender, instance, **kwargs):
    """Handle after delete events for PaymentTransaction."""
    try:
        logger.info(f"[PaymentTransactionSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PaymentTransactionSignal] after_delete error: {e}")
        raise


# ============================================================
# PENALTY TRANSACTION SIGNALS
# ============================================================

@receiver(pre_save, sender=PenaltyTransaction)
def penalty_transaction_pre_save(sender, instance, **kwargs):
    """Log before saving a penalty transaction."""
    try:
        logger.info(f"[PenaltyTransactionSignal] before_save: id={instance.id}, amount={instance.amount}, debt_id={instance.debt_id}, reason={instance.reason}")
    except Exception as e:
        logger.error(f"[PenaltyTransactionSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=PenaltyTransaction)
def penalty_transaction_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = PenaltyTransaction.objects.get(pk=instance.pk)
            instance._old_waived = getattr(old, 'waived', False)
            instance._old_reversed = getattr(old, 'reversed', False)
        except PenaltyTransaction.DoesNotExist:
            instance._old_waived = False
            instance._old_reversed = False
    else:
        instance._old_waived = False
        instance._old_reversed = False


@receiver(post_save, sender=PenaltyTransaction)
def penalty_transaction_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for PenaltyTransaction."""
    try:
        logger.info(f"[PenaltyTransactionSignal] after_save: id={instance.id}, amount={instance.amount}, debt_id={instance.debt_id}, created={created}")
        
        service = PenaltyTransactionStateTransitionService()
        
        if created:
            service.on_collect(instance, "system")
        else:
            # Check if waived
            old_waived = getattr(instance, '_old_waived', False)
            if not old_waived and getattr(instance, 'waived', False):
                service.on_waive(instance, "Admin action", "system")
            
            # Check if reversed
            old_reversed = getattr(instance, '_old_reversed', False)
            if not old_reversed and getattr(instance, 'reversed', False):
                service.on_reverse(instance, "system")
    except Exception as e:
        logger.error(f"[PenaltyTransactionSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=PenaltyTransaction)
def penalty_transaction_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for PenaltyTransaction."""
    try:
        logger.info(f"[PenaltyTransactionSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PenaltyTransactionSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=PenaltyTransaction)
def penalty_transaction_post_delete(sender, instance, **kwargs):
    """Handle after delete events for PenaltyTransaction."""
    try:
        logger.info(f"[PenaltyTransactionSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PenaltyTransactionSignal] after_delete error: {e}")
        raise