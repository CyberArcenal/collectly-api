import logging
import json
from django.db.models import Q
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from system_settings.models.system_setting import SystemSetting, SettingType
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class SystemSettingService:
    """
    Service layer for SystemSetting operations.
    Cache clearing is now handled by signals.
    """

    # ============================================================
    # GETTERS
    # ============================================================

    @staticmethod
    def get_by_id(setting_id, include_deleted=False):
        """
        Get a single setting by ID.
        """
        qs = SystemSetting.objects.all()
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=setting_id)
        except SystemSetting.DoesNotExist:
            return None

    @staticmethod
    def get_by_key(key, setting_type=None, include_deleted=False):
        """
        Get a setting by key and optional type.
        """
        qs = SystemSetting.objects.filter(key=key)
        if setting_type:
            qs = qs.filter(setting_type=setting_type)
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        return qs.first()

    @staticmethod
    def get_value(key, setting_type=None, default=None):
        """
        Get value of a setting by key.
        """
        setting = SystemSettingService.get_by_key(key, setting_type)
        if not setting:
            return default

        value = setting.value
        # Try to parse JSON
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass

        # Return as string
        return value

    @staticmethod
    def get_bool(key, setting_type=None, default=False):
        """
        Get boolean value of a setting.
        """
        value = SystemSettingService.get_value(key, setting_type)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
        return bool(value)

    @staticmethod
    def get_int(key, setting_type=None, default=0):
        """
        Get integer value of a setting.
        """
        value = SystemSettingService.get_value(key, setting_type)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_float(key, setting_type=None, default=0.0):
        """
        Get float value of a setting.
        """
        value = SystemSettingService.get_value(key, setting_type)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_list(filters=None, page=1, limit=20):
        """
        Get paginated list of settings.
        """
        qs = SystemSetting.objects.filter(deleted_at__isnull=True)

        if filters:
            if filters.get('setting_type'):
                qs = qs.filter(setting_type=filters['setting_type'])
            if filters.get('is_public') is not None:
                qs = qs.filter(is_public=filters['is_public'])
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(key__icontains=search) |
                    Q(value__icontains=search) |
                    Q(description__icontains=search)
                )

        qs = qs.order_by('setting_type', 'key')
        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_grouped():
        """
        Get all settings grouped by setting_type.
        """
        settings = SystemSetting.objects.filter(
            deleted_at__isnull=True
        ).order_by('setting_type', 'key')

        grouped = {}
        for setting in settings:
            if setting.setting_type not in grouped:
                grouped[setting.setting_type] = {}
            grouped[setting.setting_type][setting.key] = SystemSettingService.get_value(
                setting.key, setting.setting_type
            )

        return grouped

    @staticmethod
    def get_public():
        """
        Get all public settings.
        """
        settings = SystemSetting.objects.filter(
            is_public=True,
            deleted_at__isnull=True
        ).order_by('setting_type', 'key')

        result = {}
        for setting in settings:
            result[setting.key] = SystemSettingService.get_value(
                setting.key, setting.setting_type
            )

        return result

    # ============================================================
    # WRITERS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new setting.
        Cache clearing is handled by signals.
        """
        # Check if setting already exists
        existing = SystemSettingService.get_by_key(data['key'], data.get('setting_type'))
        if existing:
            raise ValidationError({'key': f'Setting already exists: {data["key"]}'})

        setting = SystemSetting.objects.create(
            key=data['key'],
            value=json.dumps(data['value']) if isinstance(data['value'], (dict, list)) else str(data['value']),
            setting_type=data.get('setting_type', SettingType.GENERAL),
            description=data.get('description'),
            is_public=data.get('is_public', False)
        )

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='config_update',
                model_name='SystemSetting',
                object_id=str(setting.id),
                changes={'data': data}
            )

        logger.info(f"System setting created: {setting.key} = {setting.value}")
        return setting

    @staticmethod
    @transaction.atomic
    def update(setting_id, data, user=None, request=None):
        """
        Update a setting.
        Cache clearing is handled by signals.
        """
        setting = SystemSettingService.get_by_id(setting_id)
        if not setting:
            raise ValidationError({'id': 'Setting not found.'})

        if 'value' in data:
            setting.value = json.dumps(data['value']) if isinstance(data['value'], (dict, list)) else str(data['value'])

        if 'description' in data:
            setting.description = data['description']

        if 'is_public' in data:
            setting.is_public = data['is_public']

        setting.save()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='config_update',
                model_name='SystemSetting',
                object_id=str(setting.id),
                changes={'data': data}
            )

        logger.info(f"System setting updated: {setting.key} = {setting.value}")
        return setting

    @staticmethod
    @transaction.atomic
    def set_value(key, value, setting_type=None, description=None, is_public=False, user=None, request=None):
        """
        Set a setting value (create or update).
        Cache clearing is handled by signals.
        """
        setting = SystemSettingService.get_by_key(key, setting_type)

        value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

        if setting:
            old_value = setting.value
            setting.value = value_str
            if description:
                setting.description = description
            setting.is_public = is_public
            setting.save()

            if user:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type='config_update',
                    model_name='SystemSetting',
                    object_id=str(setting.id),
                    changes={'old_value': old_value, 'new_value': value_str}
                )

            logger.info(f"System setting updated: {key} = {value_str}")
            return setting
        else:
            return SystemSettingService.create(
                data={
                    'key': key,
                    'value': value,
                    'setting_type': setting_type or SettingType.GENERAL,
                    'description': description,
                    'is_public': is_public,
                },
                user=user,
                request=request
            )

    @staticmethod
    @transaction.atomic
    def delete(setting_id, user=None, request=None):
        """
        Soft delete a setting.
        Cache clearing is handled by signals.
        """
        setting = SystemSettingService.get_by_id(setting_id)
        if not setting:
            raise ValidationError({'id': 'Setting not found.'})

        setting.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='config_delete',
                model_name='SystemSetting',
                object_id=str(setting.id),
                changes={'deleted_at': setting.deleted_at}
            )

        logger.info(f"System setting soft-deleted: {setting.key}")
        return setting

    @staticmethod
    @transaction.atomic
    def delete_by_key(key, setting_type=None, user=None, request=None):
        """
        Delete a setting by key.
        Cache clearing is handled by signals.
        """
        setting = SystemSettingService.get_by_key(key, setting_type)
        if not setting:
            raise ValidationError({'key': 'Setting not found.'})

        return SystemSettingService.delete(setting.id, user, request)

    # ============================================================
    # UTILITIES
    # ============================================================

    @staticmethod
    def get_system_info():
        """
        Get system information.
        """
        from django.conf import settings
        import platform

        return {
            'version': '1.0.0',
            'name': 'Collectly API',
            'environment': 'development' if settings.DEBUG else 'production',
            'debug_mode': settings.DEBUG,
            'timezone': settings.TIME_ZONE,
            'current_time': timezone.now().isoformat(),
            'python_version': platform.python_version(),
            'platform': platform.platform(),
        }