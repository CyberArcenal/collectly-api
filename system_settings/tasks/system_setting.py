# system_settings/tasks/system_setting.py
import json
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional, Dict, Any

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from system_settings.models.system_setting import SystemSetting, SettingType
from system_settings.services.setting import SystemSettingService
from system_settings.utils.base import (
    amortization_type,
    credit_check_validity_days,
    email_enabled,
    enforce_credit_check,
    get_general_settings,
    get_collections_settings,
    get_loans_settings,
    get_notifications_settings,
    get_reports_settings,
    get_integrations_settings,
    get_audit_security_settings,
    clear_settings_cache,
    get_smtp_config,
    get_twilio_config,
    log_retention_days,
    max_loan_amount,
    min_credit_score_for_approval,
    min_loan_amount,
    sms_enabled,
)
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


# ============================================================
# CACHE REFRESH TASK
# ============================================================

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


# ============================================================
# SETTINGS VALIDATION TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def validate_settings(self, setting_type: Optional[str] = None):
    """
    Validate system settings for consistency and required values.

    Checks:
    - Required settings exist
    - Email settings are complete if email_enabled is True
    - SMS settings are complete if sms_enabled is True
    - Loan settings have valid values (min < max, etc.)

    Returns:
        dict: {
            'valid': bool,
            'issues': list,
            'warnings': list
        }
    """
    logger.info(f"[SETTINGS VALIDATE] Starting validation (type={setting_type})...")

    try:
        issues = []
        warnings = []

        if setting_type is None or setting_type == SettingType.GENERAL:
            # Check general settings
            general = get_general_settings()
            if not general.get('company_name'):
                warnings.append({
                    'type': 'general',
                    'key': 'company_name',
                    'message': 'Company name is empty. Set a company name for branding.'
                })

        if setting_type is None or setting_type == SettingType.NOTIFICATIONS:
            # Check email settings
            if email_enabled():
                smtp = get_smtp_config()
                if not smtp.get('host'):
                    issues.append({
                        'type': 'email',
                        'key': 'email_smtp_host',
                        'message': 'Email is enabled but SMTP host is not configured.'
                    })
                if not smtp.get('from_email'):
                    issues.append({
                        'type': 'email',
                        'key': 'email_from_address',
                        'message': 'Email is enabled but from_email is not configured.'
                    })

            # Check SMS settings
            if sms_enabled():
                twilio = get_twilio_config()
                if not twilio.get('account_sid'):
                    issues.append({
                        'type': 'sms',
                        'key': 'twilio_account_sid',
                        'message': 'SMS is enabled but Twilio account SID is not configured.'
                    })
                if not twilio.get('auth_token'):
                    issues.append({
                        'type': 'sms',
                        'key': 'twilio_auth_token',
                        'message': 'SMS is enabled but Twilio auth token is not configured.'
                    })
                if not twilio.get('phone_number') and not twilio.get('messaging_service_sid'):
                    issues.append({
                        'type': 'sms',
                        'key': 'twilio_phone_number',
                        'message': 'SMS is enabled but no phone number or messaging service SID is configured.'
                    })

        if setting_type is None or setting_type == SettingType.COLLECTIONS:
            # Check loan amount limits
            min_amount = min_loan_amount()
            max_amount = max_loan_amount()
            if max_amount > 0 and min_amount > max_amount:
                issues.append({
                    'type': 'collections',
                    'key': 'min_loan_amount',
                    'message': f'Minimum loan amount ({min_amount}) is greater than maximum loan amount ({max_amount}).'
                })

            # Check credit check settings
            if enforce_credit_check():
                min_score = min_credit_score_for_approval()
                if min_score < 300 or min_score > 850:
                    issues.append({
                        'type': 'collections',
                        'key': 'min_credit_score_for_approval',
                        'message': f'Minimum credit score ({min_score}) is outside valid range (300-850).'
                    })

                validity_days = credit_check_validity_days()
                if validity_days < 1:
                    warnings.append({
                        'type': 'collections',
                        'key': 'credit_check_validity_days',
                        'message': 'Credit check validity days is set to 0. Credit checks will be considered expired immediately.'
                    })

        if setting_type is None or setting_type == SettingType.LOANS:
            # Check amortization type
            amortization = amortization_type()
            if amortization not in ['flat', 'declining', 'annuity']:
                warnings.append({
                    'type': 'loans',
                    'key': 'amortization_type',
                    'message': f'Amortization type "{amortization}" is not standard. Expected: flat, declining, annuity.'
                })

        if setting_type is None or setting_type == SettingType.AUDIT_SECURITY:
            # Check retention days
            retention = log_retention_days()
            if retention < 7:
                warnings.append({
                    'type': 'audit_security',
                    'key': 'log_retention_days',
                    'message': f'Log retention ({retention} days) is very short. Consider increasing to at least 30 days for compliance.'
                })

        # Determine overall validity
        valid = len(issues) == 0

        # Notify admins if critical issues found
        if issues:
            try:
                NotificationService.notify_admins_and_staff(
                    title="⚠️ Settings Validation Found Critical Issues",
                    message=f'Found {len(issues)} critical issue(s) and {len(warnings)} warning(s) in settings.',
                    type='error',
                    metadata={
                        'issues': issues,
                        'warnings': warnings[:10],
                        'valid': valid
                    },
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[SETTINGS VALIDATE] Could not send notification: {e}")

        result = {
            'valid': valid,
            'issues': issues,
            'warnings': warnings,
            'total_issues': len(issues),
            'total_warnings': len(warnings),
            'message': f'Validation complete: {len(issues)} issues, {len(warnings)} warnings'
        }

        logger.info(f"[SETTINGS VALIDATE] Completed: {result}")
        return result

    except Exception as e:
        logger.exception("[SETTINGS VALIDATE] Validation failed")
        raise self.retry(exc=e, countdown=300)


# ============================================================
# SETTINGS BACKUP TASK
# ============================================================

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
        import os
        import json
        from datetime import datetime
        from django.conf import settings

        # Get all settings
        all_settings = SystemSetting.objects.filter(deleted_at__isnull=True).values(
            'key', 'value', 'setting_type', 'description', 'is_public'
        )

        backup_data = {
            'exported_at': timezone.now().isoformat(),
            'version': '1.0',
            'settings': list(all_settings)
        }

        # Determine backup location
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'settings_backup_{timestamp}.json'
        backup_path = os.path.join(backup_dir, backup_filename)

        # Write backup
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, default=str)

        logger.info(f"[SETTINGS BACKUP] Backup saved to {backup_path}")

        # Notify admins
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


# ============================================================
# SETTINGS RESTORE TASK
# ============================================================

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
        import os
        import json

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
                    # Update or create setting
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

        # Refresh cache after restore
        clear_settings_cache()

        # Notify admins
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


# ============================================================
# SETTINGS DIFF TASK
# ============================================================

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
        import os
        import json
        from django.conf import settings

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

        # If there are differences, notify admins
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


# ============================================================
# FORCE TASKS (manual triggers)
# ============================================================

@shared_task
def force_settings_cache_refresh(setting_type: Optional[str] = None):
    """Wrapper for manual trigger of cache refresh."""
    logger.info("[SETTINGS CACHE] 🔄 Force cache refresh triggered")
    return refresh_settings_cache(setting_type=setting_type)


@shared_task
def force_settings_validate(setting_type: Optional[str] = None):
    """Wrapper for manual trigger of settings validation."""
    logger.info("[SETTINGS VALIDATE] 🔄 Force validation triggered")
    return validate_settings(setting_type=setting_type)


@shared_task
def force_settings_backup(user: str = 'system'):
    """Wrapper for manual trigger of settings backup."""
    logger.info("[SETTINGS BACKUP] 🔄 Force backup triggered")
    return backup_settings(user=user)