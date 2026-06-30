import logging
import json
from decimal import Decimal
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver
from django.core.cache import cache

from system_settings.models.system_setting import SystemSetting
from system_settings.state_transitions import SystemSettingStateTransitionService

logger = logging.getLogger(__name__)


# ============================================================
# SYSTEM SETTING SIGNALS
# ============================================================

@receiver(pre_save, sender=SystemSetting)
def system_setting_pre_save(sender, instance, **kwargs):
    """Log before saving a system setting."""
    try:
        logger.info(f"[SystemSettingSignal] before_save: id={instance.id}, key={instance.key}, setting_type={instance.setting_type}, value={instance.value[:50] if instance.value else None}...")
    except Exception as e:
        logger.error(f"[SystemSettingSignal] before_save error: {e}")
        raise


@receiver(pre_save, sender=SystemSetting)
def system_setting_pre_save_capture_old(sender, instance, **kwargs):
    """Capture old state for comparison in post_save."""
    if instance.pk:
        try:
            old = SystemSetting.objects.get(pk=instance.pk)
            instance._old_value = old.value
            instance._old_is_deleted = old.is_deleted
        except SystemSetting.DoesNotExist:
            instance._old_value = None
            instance._old_is_deleted = False
    else:
        instance._old_value = None
        instance._old_is_deleted = False


@receiver(post_save, sender=SystemSetting)
def system_setting_post_save(sender, instance, created, **kwargs):
    """
    Handle post-save events for SystemSetting.
    - On create: call on_apply
    - On update: call on_apply with old and new values
    - Clear cache
    """
    try:
        logger.info(f"[SystemSettingSignal] after_save: id={instance.id}, key={instance.key}, setting_type={instance.setting_type}, created={created}")
        
        # Clear cache
        cache.delete_pattern("setting_*")
        
        service = SystemSettingStateTransitionService()
        
        old_value = getattr(instance, '_old_value', None)
        
        if created:
            service.on_apply(instance, None, instance.value, "system")
        else:
            service.on_apply(instance, old_value, instance.value, "system")
            
            # Special handling for interest rate changes
            interest_rate_keys = ["default_interest_rate", "default_penalty_rate"]
            if instance.key in interest_rate_keys and old_value != instance.value:
                # Log interest rate change
                from debts.models.interest_rate_change_log import InterestRateChangeLog
                
                try:
                    old_val = float(old_value) if old_value else None
                    new_val = float(instance.value) if instance.value else None
                    
                    InterestRateChangeLog.objects.create(
                        setting_key=instance.key,
                        old_value=old_val,
                        new_value=new_val,
                        changed_by="system",
                        reason="Auto-logged on setting update",
                    )
                    logger.info(f"[SystemSettingSignal] Interest rate change logged: {instance.key} {old_value} -> {instance.value}")
                except Exception as e:
                    logger.error(f"[SystemSettingSignal] Failed to log interest rate change: {e}")
    except Exception as e:
        logger.error(f"[SystemSettingSignal] after_save error: {e}")
        raise


@receiver(pre_delete, sender=SystemSetting)
def system_setting_pre_delete(sender, instance, **kwargs):
    """Handle before delete events for SystemSetting."""
    try:
        logger.info(f"[SystemSettingSignal] before_delete: id={instance.id}, key={instance.key}")
    except Exception as e:
        logger.error(f"[SystemSettingSignal] before_delete error: {e}")
        raise


@receiver(post_delete, sender=SystemSetting)
def system_setting_post_delete(sender, instance, **kwargs):
    """Handle after delete events for SystemSetting."""
    try:
        logger.info(f"[SystemSettingSignal] after_delete: id={instance.id}")
        
        # Clear cache
        cache.delete_pattern("setting_*")
    except Exception as e:
        logger.error(f"[SystemSettingSignal] after_delete error: {e}")
        raise