# system_settings/tasks/diff_tasks.py
import logging
import os
import json
from typing import Optional

from celery import shared_task
from django.conf import settings

from system_settings.models.system_setting import SystemSetting
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def check_settings_diff(self, baseline_file: Optional[str] = None):
    """
    Check for differences between current settings and a baseline backup.

    Args:
        baseline_file: Optional path to baseline JSON file.
                     If None, uses the latest backup if available.

    Returns:
        dict: {
            'differences': list,
            'additions': list,
            'deletions': list,
            'modifications': list
        }
    """
    logger.info("[SETTINGS DIFF] Checking for settings changes...")

    try:
        # Get current settings
        current = list(SystemSetting.objects.filter(deleted_at__isnull=True).values(
            'key', 'value', 'setting_type'
        ))
        current_dict = {f"{s['setting_type']}:{s['key']}": s['value'] for s in current}

        # Find baseline
        if baseline_file and os.path.exists(baseline_file):
            with open(baseline_file, 'r', encoding='utf-8') as f:
                baseline_data = json.load(f)
            baseline_settings = baseline_data.get('settings', [])
        else:
            # Use the latest backup
            backup_dir = os.path.join(settings.BASE_DIR, 'backups')
            if not os.path.exists(backup_dir):
                return {
                    'message': 'No backup directory found',
                    'differences': [],
                    'additions': [],
                    'deletions': [],
                    'modifications': []
                }

            backup_files = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith('settings_backup_') and f.endswith('.json')],
                reverse=True
            )
            if not backup_files:
                return {
                    'message': 'No backup files found',
                    'differences': [],
                    'additions': [],
                    'deletions': [],
                    'modifications': []
                }

            with open(os.path.join(backup_dir, backup_files[0]), 'r', encoding='utf-8') as f:
                baseline_data = json.load(f)
            baseline_settings = baseline_data.get('settings', [])

        baseline_dict = {f"{s['setting_type']}:{s['key']}": s['value'] for s in baseline_settings}

        # Compute differences
        additions = []
        deletions = []
        modifications = []

        all_keys = set(current_dict.keys()) | set(baseline_dict.keys())

        for key in all_keys:
            if key not in current_dict:
                deletions.append(key)
            elif key not in baseline_dict:
                additions.append(key)
            elif current_dict[key] != baseline_dict[key]:
                modifications.append({
                    'key': key,
                    'old_value': baseline_dict[key],
                    'new_value': current_dict[key]
                })

        differences = additions + deletions + [m['key'] for m in modifications]

        result = {
            'differences': differences,
            'additions': additions,
            'deletions': deletions,
            'modifications': modifications,
            'total_differences': len(differences),
            'baseline_file': baseline_file or backup_files[0] if 'backup_files' in locals() else None
        }

        if result['total_differences'] > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="📊 Settings Changes Detected",
                    message=f'Found {result["total_differences"]} difference(s) in settings.',
                    type='info',
                    metadata=result,
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[SETTINGS DIFF] Could not send notification: {e}")

        logger.info(f"[SETTINGS DIFF] Completed: {result['total_differences']} differences")
        return result

    except Exception as e:
        logger.exception("[SETTINGS DIFF] Diff check failed")
        raise self.retry(exc=e, countdown=120)