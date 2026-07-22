# system_settings/tasks/validation_tasks.py
import logging
from typing import Optional

from celery import shared_task

from system_settings.models.system_setting import SettingType
from system_settings.utils.base import (
    amortization_type,
    credit_check_validity_days,
    email_enabled,
    enforce_credit_check,
    get_general_settings,
    get_smtp_config,
    get_twilio_config,
    log_retention_days,
    max_loan_amount,
    min_credit_score_for_approval,
    min_loan_amount,
    sms_enabled,
)
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def validate_settings(self, setting_type: Optional[str] = None):
    """
    Validate system settings for consistency and required values.

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
            general = get_general_settings()
            if not general.get('company_name'):
                warnings.append({
                    'type': 'general',
                    'key': 'company_name',
                    'message': 'Company name is empty. Set a company name for branding.'
                })

        if setting_type is None or setting_type == SettingType.NOTIFICATIONS:
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
            min_amount = min_loan_amount()
            max_amount = max_loan_amount()
            if max_amount > 0 and min_amount > max_amount:
                issues.append({
                    'type': 'collections',
                    'key': 'min_loan_amount',
                    'message': f'Minimum loan amount ({min_amount}) is greater than maximum loan amount ({max_amount}).'
                })

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
            amortization = amortization_type()
            if amortization not in ['flat', 'declining', 'annuity']:
                warnings.append({
                    'type': 'loans',
                    'key': 'amortization_type',
                    'message': f'Amortization type "{amortization}" is not standard. Expected: flat, declining, annuity.'
                })

        if setting_type is None or setting_type == SettingType.AUDIT_SECURITY:
            retention = log_retention_days()
            if retention < 7:
                warnings.append({
                    'type': 'audit_security',
                    'key': 'log_retention_days',
                    'message': f'Log retention ({retention} days) is very short. Consider increasing to at least 30 days for compliance.'
                })

        valid = len(issues) == 0

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


@shared_task
def force_settings_validate(setting_type: Optional[str] = None):
    """Wrapper for manual trigger of settings validation."""
    logger.info("[SETTINGS VALIDATE] 🔄 Force validation triggered")
    return validate_settings(setting_type=setting_type)