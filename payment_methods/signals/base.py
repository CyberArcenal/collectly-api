import logging
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver

from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from payment_methods.state_transitions import PaymentMethodStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# PAYMENT METHOD SIGNALS
# ============================================================

@receiver(pre_save, sender=PaymentMethod)
def payment_method_pre_save(sender, instance, **kwargs):
    """Log before saving a payment method."""
    try:
        logger.info(f"[PaymentMethodSignal] before_save: id={instance.id}, name={instance.name}, is_default={instance.is_default}")
    except Exception as e:
        logger.error(f"[PaymentMethodSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=PaymentMethod)
def payment_method_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = PaymentMethod.objects.get(pk=instance.pk)
            instance._old_is_default = old.is_default
            instance._old_name = old.name
        except PaymentMethod.DoesNotExist:
            instance._old_is_default = None
            instance._old_name = None
    else:
        instance._old_is_default = None
        instance._old_name = None


@receiver(post_save, sender=PaymentMethod)
def payment_method_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for PaymentMethod."""
    try:
        logger.info(f"[PaymentMethodSignal] after_save: id={instance.id}, name={instance.name}, is_default={instance.is_default}, created={created}")
        
        service = PaymentMethodStateTransitionService()
        
        if created:
            service.on_created(instance, "system")
        else:
            # Check if default status changed
            old_is_default = getattr(instance, '_old_is_default', None)
            if old_is_default is not None:
                service.on_update(None, instance, "system")
                if instance.is_default and not old_is_default:
                    service.on_set_default(instance, "system")
    except Exception as e:
        logger.error(f"[PaymentMethodSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=PaymentMethod)
def payment_method_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for PaymentMethod."""
    try:
        logger.info(f"[PaymentMethodSignal] before_delete: id={instance.id}, name={instance.name}")
        service = PaymentMethodStateTransitionService()
        service.on_delete(instance, "system")
    except Exception as e:
        logger.error(f"[PaymentMethodSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=PaymentMethod)
def payment_method_post_delete(sender, instance, **kwargs):
    """Handle after delete events for PaymentMethod."""
    try:
        logger.info(f"[PaymentMethodSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PaymentMethodSignal] after_delete error: {e}")
        raise


# ============================================================
# PAYMENT METHOD STAT SIGNALS
# ============================================================

@receiver(pre_save, sender=PaymentMethodStat)
def payment_method_stat_pre_save(sender, instance, **kwargs):
    """Log before saving a payment method stat."""
    try:
        logger.info(f"[PaymentMethodStatSignal] before_save: id={instance.id}, method_id={instance.method_id}, transactionCount={instance.transaction_count}")
    except Exception as e:
        logger.error(f"[PaymentMethodStatSignal] before_save error: {e}")
        raise


@receiver(post_save, sender=PaymentMethodStat)
def payment_method_stat_post_save(sender, instance, created, **kwargs):
    """Handle post-save events for PaymentMethodStat."""
    try:
        logger.info(f"[PaymentMethodStatSignal] after_save: id={instance.id}, method_id={instance.method_id}, transactionCount={instance.transaction_count}, created={created}")
        # No state transition service called in Node.js for this
    except Exception as e:
        logger.error(f"[PaymentMethodStatSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=PaymentMethodStat)
def payment_method_stat_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for PaymentMethodStat."""
    try:
        logger.info(f"[PaymentMethodStatSignal] before_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PaymentMethodStatSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=PaymentMethodStat)
def payment_method_stat_post_delete(sender, instance, **kwargs):
    """Handle after delete events for PaymentMethodStat."""
    try:
        logger.info(f"[PaymentMethodStatSignal] after_delete: id={instance.id}")
    except Exception as e:
        logger.error(f"[PaymentMethodStatSignal] after_delete error: {e}")
        raise