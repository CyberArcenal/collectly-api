# system_settings/tasks/backup_restore_tasks.py
import logging
import os
import json
from typing import Optional

from celery import shared_task
from django.db import transaction
from django.conf import settings
from django.utils import timezone

from system_settings.models.system_setting import SystemSetting, SettingType
from system_settings.services.setting import SystemSettingService
from system_settings.utils.base import clear_settings_cache
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def backup_settings(self, user: str = 'system'):
    """
    Backup all system settings to a JSON file.

    Returns:
        dict: {
            'backup_path': str,
            'settings_count': int,
            'timestamp': str
        }
    """
    logger.info("[SETTINGS BACKUP] Starting settings backup...")

    try:
        all_settings = SystemSetting.objects.filter(deleted_at__isnull=True).values(
            'key', 'value', 'setting_type', 'description', 'is_public'
        )

        backup_data = {
            'exported_at': timezone.now().isoformat(),
            'version': '1.0',
            'settings': list(all_settings)
        }

        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'settings_backup_{timestamp}.json'
        backup_path = os.path.join(backup_dir, backup_filename)

        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, default=str)

        logger.info(f"[SETTINGS BACKUP] Backup saved to {backup_path}")

        try:
            NotificationService.notify_admins_and_staff(
                title="📁 Settings Backup Completed",
                message=f'System settings backup created: {backup_filename}',
                type='info',
                metadata={
                    'backup_path': backup_path,
                    'settings_count': len(all_settings),
                    'timestamp': timestamp
                },
                user=user
            )
        except Exception as e:
            logger.warning(f"[SETTINGS BACKUP] Could not send notification: {e}")

        return {
            'backup_path': backup_path,
            'settings_count': len(all_settings),
            'timestamp': timestamp
        }

    except Exception as e:
        logger.exception("[SETTINGS BACKUP] Backup failed")
        raise self.retry(exc=e, countdown=300)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def restore_settings(self, backup_file: str, user: str = 'system'):
    """
    Restore system settings from a JSON backup file.

    Args:
        backup_file: Path to the backup JSON file

    Returns:
        dict: {
            'restored': int,
            'skipped': int,
            'errors': list
        }
    """
    logger.info(f"[SETTINGS RESTORE] Restoring settings from {backup_file}...")

    try:
        if not os.path.exists(backup_file):
            raise FileNotFoundError(f"Backup file not found: {backup_file}")

        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)

        settings_data = backup_data.get('settings', [])
        if not settings_data:
            raise ValueError("No settings found in backup file")

        restored = 0
        skipped = 0
        errors = []

        with transaction.atomic():
            for item in settings_data:
                key = item.get('key')
                value = item.get('value')
                setting_type = item.get('setting_type', SettingType.GENERAL)

                if not key or value is None:
                    skipped += 1
                    continue

                try:
                    setting = SystemSettingService.set_value(
                        key=key,
                        value=value,
                        setting_type=setting_type,
                        description=item.get('description'),
                        is_public=item.get('is_public', False),
                        user=user,
                        request=None
                    )
                    restored += 1
                except Exception as e:
                    errors.append({
                        'key': key,
                        'error': str(e)
                    })
                    logger.warning(f"[SETTINGS RESTORE] Failed to restore {key}: {e}")

        clear_settings_cache()

        try:
            NotificationService.notify_admins_and_staff(
                title="🔄 Settings Restore Completed",
                message=f'Restored {restored} settings from backup.',
                type='info' if not errors else 'error',
                metadata={
                    'restored': restored,
                    'skipped': skipped,
                    'errors': errors[:10]
                },
                user=user
            )
        except Exception as e:
            logger.warning(f"[SETTINGS RESTORE] Could not send notification: {e}")

        result = {
            'restored': restored,
            'skipped': skipped,
            'errors': errors,
            'total': len(settings_data)
        }

        logger.info(f"[SETTINGS RESTORE] Completed: {result}")
        return result

    except Exception as e:
        logger.exception("[SETTINGS RESTORE] Restore failed")
        raise self.retry(exc=e, countdown=300)


@shared_task
def force_settings_backup(user: str = 'system'):
    """Wrapper for manual trigger of settings backup."""
    logger.info("[SETTINGS BACKUP] 🔄 Force backup triggered")
    return backup_settings(user=user)