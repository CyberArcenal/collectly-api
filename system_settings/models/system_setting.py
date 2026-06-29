from django.db import models
from core.models.baseModel import BaseModel


class SettingType(models.TextChoices):
    GENERAL = 'general', 'General'
    COLLECTIONS = 'collections', 'Collections'
    LOANS = 'loans', 'Loans'
    NOTIFICATIONS = 'notifications', 'Notifications'
    REPORTS = 'reports', 'Reports'
    INTEGRATIONS = 'integrations', 'Integrations'
    AUDIT_SECURITY = 'audit_security', 'Audit & Security'


class SystemSetting(BaseModel):
    """
    System configuration settings.
    Key-value storage with type categorization.
    """
    
    key = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Setting key (e.g., 'company_name', 'default_interest_rate')"
    )
    value = models.TextField(
        help_text="Setting value (stored as text, parsed when needed)"
    )
    setting_type = models.CharField(
        max_length=20,
        choices=SettingType.choices,
        db_index=True,
        help_text="Category of the setting"
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Description of what this setting does"
    )
    is_public = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this setting is publicly readable"
    )
    
    class Meta:
        db_table = 'system_settings'
        ordering = ['setting_type', 'key']
        unique_together = [['setting_type', 'key']]
        indexes = [
            models.Index(fields=['setting_type', 'key']),
            models.Index(fields=['is_public']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return f"{self.setting_type}:{self.key}"

    @property
    def value_as_bool(self):
        """Get value as boolean."""
        if isinstance(self.value, bool):
            return self.value
        return self.value.lower() in ('true', '1', 'yes', 'on', 'enabled')

    @property
    def value_as_int(self):
        """Get value as integer."""
        try:
            return int(self.value)
        except (ValueError, TypeError):
            return 0

    @property
    def value_as_float(self):
        """Get value as float."""
        try:
            return float(self.value)
        except (ValueError, TypeError):
            return 0.0

    @property
    def value_as_json(self):
        """Get value as parsed JSON."""
        try:
            import json
            return json.loads(self.value)
        except (json.JSONDecodeError, TypeError):
            return None