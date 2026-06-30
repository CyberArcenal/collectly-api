import logging

from django.apps import AppConfig
from django.core.cache import cache
logger = logging.getLogger(__name__)

class SystemSettingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'system_settings'
    verbose_name = 'System Settings'

    def ready(self):
        """
        Import signals when app is ready.
        Also pre-warm cache if needed.
        """
        # Import signals to register them
        import system_settings.signals  # noqa

        # Optional: Pre-warm cache on startup
        self._pre_warm_cache()

    def _pre_warm_cache(self):
        """
        Pre-warm the settings cache on app startup.
        This improves performance by avoiding lazy loading.
        """
        try:
            from system_settings.models.system_setting import SystemSetting
            from system_settings.services.setting import SystemSettingService

            # Load all active settings into cache
            settings = SystemSetting.objects.filter(deleted_at__isnull=True)
            count = 0

            for setting in settings:
                cache_key = f"setting_{setting.key}"
                cache.set(cache_key, setting.value, 3600)  # 1 hour timeout

                if setting.setting_type:
                    cache_key_with_type = f"setting_{setting.setting_type}_{setting.key}"
                    cache.set(cache_key_with_type, setting.value, 3600)

                count += 1

            if count > 0:
                logger.info(f"[Settings] Pre-warmed {count} settings into cache")

        except Exception as e:
            # Log warning but don't prevent app from starting
            logger.warning(f"[Settings] Failed to pre-warm cache: {e}")