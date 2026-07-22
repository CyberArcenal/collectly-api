# system_settings/tasks/cache_tasks.py
import logging
from typing import Optional

from celery import shared_task
from django.core.cache import cache

from system_settings.models.system_setting import SystemSetting
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def refresh_settings_cache(self, setting_type: Optional[str] = None):
    """
    Refresh the settings cache by pre-loading all settings into cache.

    Args:
        setting_type: Optional filter by setting type (e.g., 'general', 'collections')

    Returns:
        dict: {
            'preloaded': int,
            'errors': list
        }
    """
    logger.info(f"[SETTINGS CACHE] Refreshing settings cache (type={setting_type})...")

    try:
        qs = SystemSetting.objects.filter(deleted_at__isnull=True)
        if setting_type:
            qs = qs.filter(setting_type=setting_type)

        count = qs.count()
        preloaded = 0
        errors = []

        for setting in qs:
            try:
                cache_key = f"setting_{setting.setting_type}_{setting.key}"
                cache.set(cache_key, setting.value, 3600)  # 1 hour
                preloaded += 1
            except Exception as e:
                errors.append({
                    'key': setting.key,
                    'setting_type': setting.setting_type,
                    'error': str(e)
                })
                logger.warning(f"[SETTINGS CACHE] Failed to cache {setting.key}: {e}")

        logger.info(f"[SETTINGS CACHE] Preloaded {preloaded} settings ({len(errors)} errors)")

        if errors:
            try:
                NotificationService.notify_admins_and_staff(
                    title="⚠️ Settings Cache Refresh Completed with Errors",
                    message=f'Preloaded {preloaded} settings, {len(errors)} errors.',
                    type='error',
                    metadata={'preloaded': preloaded, 'errors': errors[:5]},
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[SETTINGS CACHE] Could not send notification: {e}")

        return {
            'preloaded': preloaded,
            'total': count,
            'errors': errors
        }

    except Exception as e:
        logger.exception("[SETTINGS CACHE] Refresh failed")
        raise self.retry(exc=e, countdown=120)


@shared_task
def force_settings_cache_refresh(setting_type: Optional[str] = None):
    """Wrapper for manual trigger of cache refresh."""
    logger.info("[SETTINGS CACHE] 🔄 Force cache refresh triggered")
    return refresh_settings_cache(setting_type=setting_type)