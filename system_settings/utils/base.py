import json
import logging
from decimal import Decimal
from django.core.cache import cache

from system_settings.models.system_setting import SystemSetting, SettingType
from system_settings.services.setting import SystemSettingService

logger = logging.getLogger(__name__)

# Cache timeout for settings (in seconds)
SETTINGS_CACHE_TIMEOUT = 3600  # 1 hour


# ============================================================
# 📊 CORE GETTER FUNCTIONS
# ============================================================

def get_value(key, setting_type=None, default=None):
    """
    Get setting value from database or cache.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        str: Setting value as string
    """
    try:
        # Try cache first
        cache_key = f"setting_{setting_type}_{key}" if setting_type else f"setting_{key}"
        cached_value = cache.get(cache_key)
        
        if cached_value is not None:
            return cached_value
        
        # Get from database
        setting = SystemSettingService.get_by_key(key, setting_type)
        
        if not setting:
            logger.debug(f"[Settings] Setting {key} not found, using default: {default}")
            return default
        
        # Cache the value
        cache.set(cache_key, setting.value, SETTINGS_CACHE_TIMEOUT)
        
        return setting.value
        
    except Exception as e:
        logger.warning(f"[Settings] Error fetching setting {key}: {e}, using default: {default}")
        return default


def get_bool(key, setting_type=None, default=False):
    """
    Get boolean setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        bool: Setting value as boolean
    """
    try:
        raw = get_value(key, setting_type, "true" if default else "false")
        if raw is None:
            return default
        
        normalized = str(raw).strip().lower()
        
        # Check for truthy values
        if normalized in ["true", "1", "yes", "y", "on", "enabled", "active"]:
            return True
        
        # Check for falsy values
        if normalized in ["false", "0", "no", "n", "off", "disabled", "inactive"]:
            return False
        
        # Try parsing as number
        try:
            num = float(normalized)
            return num > 0
        except ValueError:
            pass
        
        logger.warning(f"[Settings] Unrecognized boolean for key '{key}': '{raw}' → using default={default}")
        return default
        
    except Exception as e:
        logger.error(f"[Settings] Error in get_bool for {key}: {e}, using default: {default}")
        return default


def get_int(key, setting_type=None, default=0):
    """
    Get integer setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        int: Setting value as integer
    """
    try:
        raw = get_value(key, setting_type, str(default))
        if raw is None:
            return default
        
        result = int(str(raw).strip())
        return result
        
    except (ValueError, TypeError) as e:
        logger.warning(f"[Settings] Invalid int for key '{key}': {e} → using default={default}")
        return default


def get_float(key, setting_type=None, default=0.0):
    """
    Get float setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        float: Setting value as float
    """
    try:
        raw = get_value(key, setting_type, str(default))
        if raw is None:
            return default
        
        result = float(str(raw).strip())
        return result
        
    except (ValueError, TypeError) as e:
        logger.warning(f"[Settings] Invalid float for key '{key}': {e} → using default={default}")
        return default


def get_decimal(key, setting_type=None, default=Decimal('0')):
    """
    Get Decimal setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        Decimal: Setting value as Decimal
    """
    try:
        raw = get_value(key, setting_type, str(default))
        if raw is None:
            return default
        
        result = Decimal(str(raw).strip())
        return result
        
    except Exception as e:
        logger.warning(f"[Settings] Invalid Decimal for key '{key}': {e} → using default={default}")
        return default


def get_array(key, setting_type=None, default=None):
    """
    Get array/list setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        list: Setting value as list
    """
    if default is None:
        default = []
    
    try:
        raw = get_value(key, setting_type, json.dumps(default))
        if raw is None:
            return default
        
        # If already a list, return it
        if isinstance(raw, list):
            return raw
        
        # Try parsing as JSON
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        
        return default
        
    except Exception as e:
        logger.warning(f"[Settings] Error getting array setting {key}: {e}, using default")
        return default


def get_json(key, setting_type=None, default=None):
    """
    Get JSON setting value.
    
    Args:
        key: Setting key
        setting_type: Optional setting type filter
        default: Default value if not found
    
    Returns:
        dict/list: Setting value as parsed JSON
    """
    if default is None:
        default = {}
    
    try:
        raw = get_value(key, setting_type, json.dumps(default))
        if raw is None:
            return default
        
        # If already a dict/list, return it
        if isinstance(raw, (dict, list)):
            return raw
        
        # Try parsing as JSON
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        
        return default
        
    except Exception as e:
        logger.warning(f"[Settings] Error getting JSON setting {key}: {e}, using default")
        return default


# ============================================================
# 🏢 GENERAL SETTINGS
# ============================================================

def company_name():
    return get_value("company_name", SettingType.GENERAL, "Collectly")


def branch_location():
    return get_value("branch_location", SettingType.GENERAL, "")


def default_timezone():
    return get_value("default_timezone", SettingType.GENERAL, "Asia/Manila")


def language():
    return get_value("language", SettingType.GENERAL, "en")


def currency():
    return get_value("currency", SettingType.GENERAL, "PHP")


def receipt_footer_message():
    return get_value("receipt_footer_message", SettingType.GENERAL, "")


def auto_logout_minutes():
    return get_int("auto_logout_minutes", SettingType.GENERAL, 30)


def date_format():
    return get_value("date_format", SettingType.GENERAL, "YYYY-MM-DD")


# ============================================================
# 📋 COLLECTIONS SETTINGS
# ============================================================

def default_interest_rate():
    return get_int("default_interest_rate", SettingType.COLLECTIONS, 10)


def default_penalty_rate():
    return get_float("default_penalty_rate", SettingType.COLLECTIONS, 2.0)


def penalty_calculation_method():
    return get_value("penalty_calculation_method", SettingType.COLLECTIONS, "percentage")


def enable_auto_penalty():
    return get_bool("enable_auto_penalty", SettingType.COLLECTIONS, True)


def penalty_grace_days():
    return get_int("penalty_grace_days", SettingType.COLLECTIONS, 0)


def overdue_reminder_days():
    return get_array("overdue_reminder_days", SettingType.COLLECTIONS, [7, 3, 1])


def max_loan_amount():
    return get_int("max_loan_amount", SettingType.COLLECTIONS, 0)


def min_loan_amount():
    return get_int("min_loan_amount", SettingType.COLLECTIONS, 0)


def enforce_credit_check():
    return get_bool("enforce_credit_check", SettingType.COLLECTIONS, False)


def credit_check_validity_days():
    return get_int("credit_check_validity_days", SettingType.COLLECTIONS, 30)


def min_credit_score_for_approval():
    return get_int("min_credit_score_for_approval", SettingType.COLLECTIONS, 0)


def default_interest_calculation_period():
    return get_value("interest_calculation_period", SettingType.COLLECTIONS, "per_annum")


# ============================================================
# 💰 LOANS SETTINGS
# ============================================================

def allowed_loan_statuses():
    return get_array("allowed_loan_statuses", SettingType.LOANS, ["active", "paid", "overdue", "defaulted"])


def enable_partial_payment():
    return get_bool("enable_partial_payment", SettingType.LOANS, True)


def enable_early_payment_discount():
    return get_bool("enable_early_payment_discount", SettingType.LOANS, False)


def early_payment_discount_rate():
    return get_int("early_payment_discount_rate", SettingType.LOANS, 0)


def require_loan_agreement():
    return get_bool("require_loan_agreement", SettingType.LOANS, False)


def loan_agreement_template():
    return get_value("loan_agreement_template", SettingType.LOANS, "")


def amortization_type():
    return get_value("amortization_type", SettingType.LOANS, "flat")


def default_loan_term_months():
    return get_int("default_loan_term_months", SettingType.LOANS, 12)


# ============================================================
# 🔔 NOTIFICATION SETTINGS
# ============================================================

def email_enabled():
    return get_bool("email_enabled", SettingType.NOTIFICATIONS, False)


def sms_enabled():
    return get_bool("sms_enabled", SettingType.NOTIFICATIONS, False)


def sms_provider():
    return get_value("sms_provider", SettingType.NOTIFICATIONS, "twilio")


def reminder_days_before_due():
    return get_array("reminder_days_before_due", SettingType.NOTIFICATIONS, [7, 3, 1])


def overdue_notification_frequency():
    return get_value("overdue_notification_frequency", SettingType.NOTIFICATIONS, "daily")


def notify_on_payment():
    return get_bool("notify_on_payment", SettingType.NOTIFICATIONS, True)


def notify_on_penalty():
    return get_bool("notify_on_penalty", SettingType.NOTIFICATIONS, True)


# SMTP Settings
def smtp_host():
    return get_value("email_smtp_host", SettingType.NOTIFICATIONS, "")


def smtp_port():
    return get_int("email_smtp_port", SettingType.NOTIFICATIONS, 587)


def smtp_username():
    return get_value("email_smtp_username", SettingType.NOTIFICATIONS, "")


def smtp_password():
    return get_value("email_smtp_password", SettingType.NOTIFICATIONS, "")


def smtp_from_email():
    return get_value("email_from_address", SettingType.NOTIFICATIONS, "")


def smtp_from_name():
    return get_value("email_from_name", SettingType.NOTIFICATIONS, "")


def get_smtp_config():
    """Get SMTP configuration as a dict."""
    return {
        "host": smtp_host(),
        "port": smtp_port(),
        "username": smtp_username(),
        "password": smtp_password(),
        "from_email": smtp_from_email(),
        "from_name": smtp_from_name(),
    }


# Twilio SMS Settings
def twilio_account_sid():
    return get_value("twilio_account_sid", SettingType.NOTIFICATIONS, "")


def twilio_auth_token():
    return get_value("twilio_auth_token", SettingType.NOTIFICATIONS, "")


def twilio_phone_number():
    return get_value("twilio_phone_number", SettingType.NOTIFICATIONS, "")


def twilio_messaging_service_sid():
    return get_value("twilio_messaging_service_sid", SettingType.NOTIFICATIONS, "")


def get_twilio_config():
    """Get Twilio configuration as a dict."""
    return {
        "account_sid": twilio_account_sid(),
        "auth_token": twilio_auth_token(),
        "phone_number": twilio_phone_number(),
        "messaging_service_sid": twilio_messaging_service_sid(),
    }


# ============================================================
# 📊 REPORTS SETTINGS
# ============================================================

def export_formats():
    return get_array("export_formats", SettingType.REPORTS, ["CSV", "Excel", "PDF"])


def default_export_format():
    return get_value("default_export_format", SettingType.REPORTS, "CSV")


def auto_backup_enabled():
    return get_bool("auto_backup_enabled", SettingType.REPORTS, False)


def backup_schedule():
    return get_value("backup_schedule", SettingType.REPORTS, "0 2 * * *")


def backup_location():
    return get_value("backup_location", SettingType.REPORTS, "./backups")


def data_retention_days():
    return get_int("data_retention_days", SettingType.REPORTS, 365)


def include_audit_in_backup():
    return get_bool("include_audit_in_backup", SettingType.REPORTS, False)


# ============================================================
# 🔗 INTEGRATIONS SETTINGS
# ============================================================

def accounting_integration_enabled():
    return get_bool("accounting_integration_enabled", SettingType.INTEGRATIONS, False)


def accounting_api_url():
    return get_value("accounting_api_url", SettingType.INTEGRATIONS, "")


def accounting_api_key():
    return get_value("accounting_api_key", SettingType.INTEGRATIONS, "")


def credit_bureau_api_enabled():
    return get_bool("credit_bureau_api_enabled", SettingType.INTEGRATIONS, False)


def credit_bureau_api_key():
    return get_value("credit_bureau_api_key", SettingType.INTEGRATIONS, "")


def credit_bureau_endpoint():
    return get_value("credit_bureau_endpoint", SettingType.INTEGRATIONS, "")


def webhooks_enabled():
    return get_bool("webhooks_enabled", SettingType.INTEGRATIONS, False)


def webhooks():
    return get_array("webhooks", SettingType.INTEGRATIONS, [])


# ============================================================
# 🔒 AUDIT & SECURITY SETTINGS
# ============================================================

def audit_log_enabled():
    return get_bool("audit_log_enabled", SettingType.AUDIT_SECURITY, True)


def log_retention_days():
    return get_int("log_retention_days", SettingType.AUDIT_SECURITY, 30)


def log_events():
    return get_array("log_events", SettingType.AUDIT_SECURITY, ["CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT"])


def force_https():
    return get_bool("force_https", SettingType.AUDIT_SECURITY, False)


def session_encryption_enabled():
    return get_bool("session_encryption_enabled", SettingType.AUDIT_SECURITY, True)


def gdpr_compliance_enabled():
    return get_bool("gdpr_compliance_enabled", SettingType.AUDIT_SECURITY, False)


def require_mfa_for_admin():
    return get_bool("require_mfa_for_admin", SettingType.AUDIT_SECURITY, False)


# ============================================================
# 📦 CATEGORY-LEVEL CONVENIENCE FUNCTIONS
# ============================================================

def get_general_settings():
    """Get all general settings as a dict."""
    return {
        "company_name": company_name(),
        "branch_location": branch_location(),
        "default_timezone": default_timezone(),
        "currency": currency(),
        "language": language(),
        "receipt_footer_message": receipt_footer_message(),
        "auto_logout_minutes": auto_logout_minutes(),
        "date_format": date_format(),
    }


def get_collections_settings():
    """Get all collections settings as a dict."""
    return {
        "default_interest_rate": default_interest_rate(),
        "default_penalty_rate": default_penalty_rate(),
        "penalty_calculation_method": penalty_calculation_method(),
        "enable_auto_penalty": enable_auto_penalty(),
        "penalty_grace_days": penalty_grace_days(),
        "overdue_reminder_days": overdue_reminder_days(),
        "max_loan_amount": max_loan_amount(),
        "min_loan_amount": min_loan_amount(),
        "enforce_credit_check": enforce_credit_check(),
        "credit_check_validity_days": credit_check_validity_days(),
        "min_credit_score_for_approval": min_credit_score_for_approval(),
        "interest_calculation_period": default_interest_calculation_period(),
    }


def get_loans_settings():
    """Get all loans settings as a dict."""
    return {
        "allowed_loan_statuses": allowed_loan_statuses(),
        "enable_partial_payment": enable_partial_payment(),
        "enable_early_payment_discount": enable_early_payment_discount(),
        "early_payment_discount_rate": early_payment_discount_rate(),
        "require_loan_agreement": require_loan_agreement(),
        "loan_agreement_template": loan_agreement_template(),
        "amortization_type": amortization_type(),
        "default_loan_term_months": default_loan_term_months(),
    }


def get_notifications_settings():
    """Get all notification settings as a dict."""
    return {
        "email_enabled": email_enabled(),
        "sms_enabled": sms_enabled(),
        "sms_provider": sms_provider(),
        "reminder_days_before_due": reminder_days_before_due(),
        "overdue_notification_frequency": overdue_notification_frequency(),
        "notify_on_payment": notify_on_payment(),
        "notify_on_penalty": notify_on_penalty(),
        "smtp": get_smtp_config(),
        "twilio": get_twilio_config(),
    }


def get_reports_settings():
    """Get all reports settings as a dict."""
    return {
        "export_formats": export_formats(),
        "default_export_format": default_export_format(),
        "auto_backup_enabled": auto_backup_enabled(),
        "backup_schedule": backup_schedule(),
        "backup_location": backup_location(),
        "data_retention_days": data_retention_days(),
        "include_audit_in_backup": include_audit_in_backup(),
    }


def get_integrations_settings():
    """Get all integrations settings as a dict."""
    return {
        "accounting_integration_enabled": accounting_integration_enabled(),
        "accounting_api_url": accounting_api_url(),
        "accounting_api_key": accounting_api_key(),
        "credit_bureau_api_enabled": credit_bureau_api_enabled(),
        "credit_bureau_api_key": credit_bureau_api_key(),
        "credit_bureau_endpoint": credit_bureau_endpoint(),
        "webhooks_enabled": webhooks_enabled(),
        "webhooks": webhooks(),
    }


def get_audit_security_settings():
    """Get all audit & security settings as a dict."""
    return {
        "audit_log_enabled": audit_log_enabled(),
        "log_retention_days": log_retention_days(),
        "log_events": log_events(),
        "force_https": force_https(),
        "session_encryption_enabled": session_encryption_enabled(),
        "gdpr_compliance_enabled": gdpr_compliance_enabled(),
        "require_mfa_for_admin": require_mfa_for_admin(),
    }


# ============================================================
# 🔄 SYNC SETTINGS (hybrid mode)
# ============================================================

def sync_mode():
    """
    Get current sync mode (offline/online).
    
    Returns:
        str: "offline" or "online"
    """
    return get_value("sync_mode", SettingType.GENERAL, "offline")


def server_url():
    """
    Get server URL for online sync.
    
    Returns:
        str: Server URL
    """
    return get_value("server_url", SettingType.GENERAL, "")


def set_sync_settings(mode, url=""):
    """
    Save sync mode and server URL.
    
    Args:
        mode: 'offline' or 'online'
        url: Server URL (required when mode === 'online')
    """
    # Save sync_mode
    SystemSettingService.set_value(
        key="sync_mode",
        value=mode,
        setting_type=SettingType.GENERAL,
        description="Offline/Online mode for hybrid sync",
        is_public=True,
    )
    
    # Save server_url (if mode === 'online' and url provided; otherwise clear it)
    if mode == "online" and url:
        SystemSettingService.set_value(
            key="server_url",
            value=url,
            setting_type=SettingType.GENERAL,
            description="Server URL for online sync",
            is_public=True,
        )
    elif mode == "offline":
        # Clear the stored server URL when switching offline
        SystemSettingService.set_value(
            key="server_url",
            value="",
            setting_type=SettingType.GENERAL,
            description="Server URL for online sync",
            is_public=True,
        )


# ============================================================
# 📤 GENERIC GETTER
# ============================================================

def get_system_setting(key, fallback=None):
    """
    Get a system setting by key (any category).
    
    Args:
        key: The setting key
        fallback: Default value if not found
    
    Returns:
        any: Setting value
    """
    return get_value(key, None, fallback)


# ============================================================
# 🔄 CLEAR CACHE
# ============================================================

def clear_settings_cache():
    """
    Clear all cached settings.
    Use this after updating settings to refresh the cache.
    """
    try:
        cache.delete_pattern("setting_*")
        logger.info("[Settings] Cache cleared successfully")
    except Exception as e:
        logger.warning(f"[Settings] Failed to clear cache: {e}")