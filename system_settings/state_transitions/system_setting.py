import logging
import json
from decimal import Decimal
from django.db import transaction
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings

from audit.utils.log import log_audit_event
from system_settings.models.system_setting import SystemSetting, SettingType

logger = logging.getLogger(__name__)


class SystemSettingStateTransitionService:
    """
    Service for handling system setting state transitions.

    Handles applying settings changes, resetting to defaults, and validation.
    Manages cache invalidation and service reloading.
    """

    # ============================================================
    # CONSTANTS - DEFAULT VALUES
    # ============================================================

    DEFAULTS = {
        # General
        'company_name': 'Collectly',
        'branch_location': '',
        'default_timezone': 'Asia/Manila',
        'currency': 'PHP',
        'language': 'en',
        'receipt_footer_message': '',
        'auto_logout_minutes': 30,
        'date_format': 'YYYY-MM-DD',

        # Collections
        'default_interest_rate': 10,
        'default_penalty_rate': 2,
        'penalty_calculation_method': 'percentage',
        'enable_auto_penalty': True,
        'penalty_grace_days': 0,
        'overdue_reminder_days': [7, 3, 1],
        'max_loan_amount': 0,
        'min_loan_amount': 0,
        'enforce_credit_check': False,
        'credit_check_validity_days': 30,
        'min_credit_score_for_approval': 0,
        'interest_calculation_period': 'per_annum',

        # Loans
        'allowed_loan_statuses': ['active', 'paid', 'overdue', 'defaulted'],
        'enable_partial_payment': True,
        'enable_early_payment_discount': False,
        'early_payment_discount_rate': 0,
        'require_loan_agreement': False,
        'loan_agreement_template': '',
        'amortization_type': 'flat',
        'default_loan_term_months': 12,

        # Notifications
        'email_enabled': False,
        'sms_enabled': False,
        'sms_provider': 'twilio',
        'reminder_days_before_due': [7, 3, 1],
        'overdue_notification_frequency': 'daily',
        'notify_on_payment': True,
        'notify_on_penalty': True,
        'email_smtp_host': '',
        'email_smtp_port': 587,
        'email_smtp_username': '',
        'email_smtp_password': '',
        'email_from_address': '',
        'email_from_name': '',

        # Twilio
        'twilio_account_sid': '',
        'twilio_auth_token': '',
        'twilio_phone_number': '',
        'twilio_messaging_service_sid': '',

        # Reports
        'export_formats': ['CSV', 'Excel', 'PDF'],
        'default_export_format': 'CSV',
        'auto_backup_enabled': False,
        'backup_schedule': '0 2 * * *',
        'backup_location': './backups',
        'data_retention_days': 365,
        'include_audit_in_backup': False,

        # Integrations
        'accounting_integration_enabled': False,
        'accounting_api_url': '',
        'accounting_api_key': '',
        'credit_bureau_api_enabled': False,
        'credit_bureau_api_key': '',
        'credit_bureau_endpoint': '',
        'webhooks_enabled': False,
        'webhooks': [],

        # Audit & Security
        'audit_log_enabled': True,
        'log_retention_days': 30,
        'log_events': ['CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT'],
        'force_https': False,
        'session_encryption_enabled': True,
        'gdpr_compliance_enabled': False,
        'require_mfa_for_admin': False,

        # Sync
        'sync_mode': 'offline',
        'server_url': '',
    }

    # ============================================================
    # CACHE HELPERS
    # ============================================================

    @staticmethod
    def _get_cache_key(key, setting_type=None):
        """
        Get cache key for a setting.

        Args:
            key: Setting key
            setting_type: Optional setting type

        Returns:
            str: Cache key
        """
        if setting_type:
            return f"setting_{setting_type}_{key}"
        return f"setting_{key}"

    @staticmethod
    def _invalidate_cache(key, setting_type=None):
        """
        Invalidate cache for a specific setting.

        Args:
            key: Setting key
            setting_type: Optional setting type
        """
        # Delete specific cache keys
        cache.delete(SystemSettingStateTransitionService._get_cache_key(key, setting_type))
        cache.delete(SystemSettingStateTransitionService._get_cache_key(key))

        # Delete all settings cache patterns
        try:
            cache.delete_pattern("setting_*")
        except AttributeError:
            # Fallback: clear whole cache when pattern delete is unavailable
            try:
                cache.clear()
            except Exception:
                logger.exception("[SettingsCache] Failed to clear cache fallback")
                
        logger.debug(f"[SettingsCache] Invalidated cache for: {key}")

    # ============================================================
    # SERVICE RELOADING
    # ============================================================

    @staticmethod
    def _reload_service(setting_key, old_value=None, new_value=None):
        """
        Reload services that depend on the changed setting.

        Args:
            setting_key: The setting key that changed
            old_value: Old value (optional)
            new_value: New value (optional)
        """
        # Email settings
        if setting_key.startswith('email_') or setting_key == 'email_enabled':
            logger.info(f"[SystemSetting] Email settings changed, will affect future sends.")
            # In Django, email settings are typically read from settings.py
            # or from database on each request via utils

        # SMS/Twilio settings
        if setting_key.startswith('twilio_') or setting_key == 'sms_enabled':
            logger.info(f"[SystemSetting] SMS/Twilio settings changed.")

        # Currency setting - notify frontend
        if setting_key == 'currency':
            logger.info(f"[SystemSetting] Currency changed to {new_value}, UI should refresh.")

        # Interest rate changes - may need to recalculate debts
        if setting_key in ['default_interest_rate', 'default_penalty_rate']:
            logger.info(
                f"[SystemSetting] {setting_key} changed from {old_value} to {new_value}. "
                f"Consider updating active loans if policy requires."
            )

        # Amortization type change
        if setting_key == 'amortization_type':
            logger.info(
                f"[SystemSetting] Amortization type changed to {new_value}. "
                f"This will affect future interest calculations."
            )

        # Sync mode change
        if setting_key == 'sync_mode':
            logger.info(f"[SystemSetting] Sync mode changed to {new_value}.")

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_apply(setting, old_value, new_value, user="system", request=None):
        """
        Apply a setting change (invalidate cache, reload services, audit log).

        Args:
            setting: SystemSetting instance
            old_value: Old value
            new_value: New value
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            SystemSetting: The updated setting instance
        """
        logger.info(
            f"[SystemSettingTransition] on_apply: "
            f"key={setting.key}, old={old_value}, new={new_value}, user={user}"
        )

        # 1. Invalidate cache
        SystemSettingStateTransitionService._invalidate_cache(
            setting.key,
            setting.setting_type
        )

        # 2. Reload services that depend on this setting
        SystemSettingStateTransitionService._reload_service(
            setting.key,
            old_value,
            new_value
        )

        # 3. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='config_update',
            model_name='SystemSetting',
            object_id=str(setting.id),
            changes={
                'key': setting.key,
                'setting_type': setting.setting_type,
                'old_value': old_value,
                'new_value': new_value,
                'applied': True,
            }
        )

        logger.info(
            f"[SystemSettingTransition] Setting applied: {setting.key} = {new_value}"
        )

        return setting

    @staticmethod
    @transaction.atomic
    def on_reset(setting, user="system", request=None):
        """
        Reset setting to factory default.

        Args:
            setting: SystemSetting instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            SystemSetting: The reset setting instance

        Raises:
            ValidationError: If no default value defined
        """
        logger.info(
            f"[SystemSettingTransition] on_reset: "
            f"key={setting.key}, user={user}"
        )

        # 1. Fetch default value from constants
        default_value = SystemSettingStateTransitionService.DEFAULTS.get(setting.key)

        if default_value is None:
            raise ValidationError({
                'detail': f'No default value defined for key: {setting.key}'
            })

        # 2. Store old value
        old_value = setting.value

        # 3. Prepare and save default value
        setting.value = SystemSettingStateTransitionService._prepare_value_for_storage(default_value)
        setting.updated_at = timezone.now()
        setting.save(update_fields=['value', 'updated_at'])

        # 4. Invalidate cache
        SystemSettingStateTransitionService._invalidate_cache(
            setting.key,
            setting.setting_type
        )

        # 5. Reload services
        SystemSettingStateTransitionService._reload_service(
            setting.key,
            old_value,
            default_value
        )

        # 6. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='config_reset',
            model_name='SystemSetting',
            object_id=str(setting.id),
            changes={
                'key': setting.key,
                'setting_type': setting.setting_type,
                'old_value': old_value,
                'new_value': default_value,
                'reset': True,
            }
        )

        logger.info(
            f"[SystemSettingTransition] Setting reset: {setting.key} = {default_value}"
        )

        return setting

    @staticmethod
    def on_validate(setting, proposed_value):
        """
        Validate a proposed value before applying.

        Args:
            setting: SystemSetting instance
            proposed_value: Proposed new value

        Returns:
            dict: {
                'valid': bool,
                'error_message': str or None
            }
        """
        logger.info(
            f"[SystemSettingTransition] on_validate: "
            f"key={setting.key}, value={proposed_value}"
        )

        # Convert to string for validation
        value_str = str(proposed_value).strip() if proposed_value is not None else ""
        key = setting.key

        # ============================================================
        # BOOLEAN VALIDATION
        # ============================================================

        boolean_keys = [
            'email_enabled', 'sms_enabled', 'enable_auto_penalty',
            'enforce_credit_check', 'enable_partial_payment',
            'enable_early_payment_discount', 'require_loan_agreement',
            'auto_backup_enabled', 'include_audit_in_backup',
            'audit_log_enabled', 'force_https', 'session_encryption_enabled',
            'gdpr_compliance_enabled', 'require_mfa_for_admin',
            'webhooks_enabled', 'accounting_integration_enabled',
            'credit_bureau_api_enabled', 'notify_on_payment', 'notify_on_penalty',
        ]

        if key in boolean_keys:
            bool_val = value_str.lower()
            if bool_val in ['true', 'false', '1', '0', 'yes', 'no']:
                return {'valid': True, 'error_message': None}
            return {
                'valid': False,
                'error_message': 'Must be a boolean (true/false, yes/no, 1/0)'
            }

        # ============================================================
        # NUMERIC VALIDATION
        # ============================================================

        numeric_keys = [
            'default_interest_rate', 'default_penalty_rate', 'penalty_grace_days',
            'max_loan_amount', 'min_loan_amount', 'early_payment_discount_rate',
            'default_loan_term_months', 'email_smtp_port', 'auto_logout_minutes',
            'log_retention_days', 'data_retention_days', 'credit_check_validity_days',
            'min_credit_score_for_approval',
        ]

        if key in numeric_keys:
            try:
                num = float(value_str)
            except ValueError:
                return {
                    'valid': False,
                    'error_message': 'Must be a number'
                }

            # Port range
            if key == 'email_smtp_port' and (num < 1 or num > 65535):
                return {
                    'valid': False,
                    'error_message': 'Port must be between 1 and 65535'
                }

            # Interest rate range
            if key in ['default_interest_rate', 'default_penalty_rate', 'early_payment_discount_rate']:
                if num < 0 or num > 100:
                    return {
                        'valid': False,
                        'error_message': 'Rate must be between 0 and 100'
                    }

            # Auto logout range
            if key == 'auto_logout_minutes' and (num < 0 or num > 1440):
                return {
                    'valid': False,
                    'error_message': 'Auto logout must be between 0 and 1440 minutes'
                }

            # Retention days
            if key == 'data_retention_days' and num < 0:
                return {
                    'valid': False,
                    'error_message': 'Retention days cannot be negative'
                }

            # Credit score range
            if key == 'min_credit_score_for_approval':
                if num < 0 or num > 850:
                    return {
                        'valid': False,
                        'error_message': 'Minimum credit score must be between 0 and 850'
                    }

            return {'valid': True, 'error_message': None}

        # ============================================================
        # EMAIL ADDRESS VALIDATION
        # ============================================================

        if key == 'email_from_address' and value_str != "":
            import re
            email_regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
            if not re.match(email_regex, value_str):
                return {
                    'valid': False,
                    'error_message': 'Invalid email address format'
                }

        # ============================================================
        # JSON ARRAY VALIDATION
        # ============================================================

        json_array_keys = [
            'allowed_loan_statuses', 'export_formats', 'log_events',
            'overdue_reminder_days', 'reminder_days_before_due', 'webhooks',
        ]

        if key in json_array_keys:
            try:
                parsed = json.loads(value_str)
                if not isinstance(parsed, list):
                    return {
                        'valid': False,
                        'error_message': 'Must be a JSON array'
                    }
            except json.JSONDecodeError:
                return {
                    'valid': False,
                    'error_message': 'Must be a valid JSON array'
                }

        # ============================================================
        # TIMEZONE VALIDATION
        # ============================================================

        if key == 'default_timezone':
            try:
                import pytz # type: ignore
                pytz.timezone(value_str)
            except (ImportError, pytz.UnknownTimeZoneError):
                # If pytz not available, try using django's timezone
                try:
                    from django.utils import timezone as django_timezone
                    django_timezone.get_fixed_timezone(value_str)
                except Exception:
                    return {
                        'valid': False,
                        'error_message': 'Invalid timezone'
                    }

        # ============================================================
        # URL VALIDATION
        # ============================================================

        if key in ['server_url', 'accounting_api_url', 'credit_bureau_endpoint']:
            if value_str and not value_str.startswith(('http://', 'https://')):
                return {
                    'valid': False,
                    'error_message': 'Must be a valid URL starting with http:// or https://'
                }

        # ============================================================
        # CRON EXPRESSION VALIDATION
        # ============================================================

        if key == 'backup_schedule':
            # Simple validation - check if it has 5 parts
            parts = value_str.split()
            if value_str and len(parts) != 5:
                return {
                    'valid': False,
                    'error_message': 'Must be a valid cron expression (5 parts)'
                }

        # ============================================================
        # PATH VALIDATION
        # ============================================================

        if key == 'backup_location':
            # Just check if it's not empty, paths can be validated at runtime
            pass

        # ============================================================
        # DEFAULT - ACCEPT ANY STRING
        # ============================================================

        return {'valid': True, 'error_message': None}

    # ============================================================
    # UTILITY METHODS
    # ============================================================

    @staticmethod
    def _prepare_value_for_storage(value):
        """
        Prepare a value for storage in the database.

        Args:
            value: Value to prepare

        Returns:
            str: String representation for storage
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    @staticmethod
    def _parse_value(value):
        """
        Parse a stored value back to its original type.

        Args:
            value: Stored string value

        Returns:
            Parsed value (bool, int, float, list, dict, or str)
        """
        if value is None:
            return None

        # Try boolean
        if value.lower() in ['true', 'false']:
            return value.lower() == 'true'

        # Try integer
        try:
            return int(value)
        except ValueError:
            pass

        # Try float
        try:
            return float(value)
        except ValueError:
            pass

        # Try JSON
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

        # Return as string
        return value

    @staticmethod
    def get_default_value(key):
        """
        Get default value for a setting key.

        Args:
            key: Setting key

        Returns:
            Default value or None if not found
        """
        return SystemSettingStateTransitionService.DEFAULTS.get(key)

    @staticmethod
    def get_all_defaults():
        """
        Get all default values.

        Returns:
            dict: All default values
        """
        return SystemSettingStateTransitionService.DEFAULTS.copy()

    @staticmethod
    def is_default(value, key):
        """
        Check if a value matches the default for a key.

        Args:
            value: Value to check
            key: Setting key

        Returns:
            bool: True if value equals default
        """
        default = SystemSettingStateTransitionService.DEFAULTS.get(key)
        if default is None:
            return False

        # Compare after normalizing
        if isinstance(default, bool):
            return bool(value) == default
        if isinstance(default, (list, dict)):
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
                return parsed == default
            except (json.JSONDecodeError, TypeError):
                return False
        try:
            return float(value) == float(default)
        except (ValueError, TypeError):
            return str(value) == str(default)