import json
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from system_settings.models.system_setting import SystemSetting, SettingType


class SystemSettingReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for system setting detail view.
    """
    
    setting_type_display = serializers.CharField(source='get_setting_type_display', read_only=True)
    value_parsed = serializers.SerializerMethodField()
    value_as_bool = serializers.SerializerMethodField()
    value_as_int = serializers.SerializerMethodField()
    value_as_float = serializers.SerializerMethodField()
    value_as_json = serializers.SerializerMethodField()

    # ✅ CamelCase fields for frontend compatibility
    isPublic = serializers.BooleanField(source='is_public', read_only=True)
    settingType = serializers.CharField(source='setting_type', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)
    settingTypeDisplay = serializers.CharField(source='get_setting_type_display', read_only=True)
    valueParsed = serializers.SerializerMethodField()
    valueAsBool = serializers.SerializerMethodField()
    valueAsInt = serializers.SerializerMethodField()
    valueAsFloat = serializers.SerializerMethodField()
    valueAsJson = serializers.SerializerMethodField()

    class Meta:
        model = SystemSetting
        fields = [
            'id',
            'key',
            'value',
            'value_parsed',
            'value_as_bool',
            'value_as_int',
            'value_as_float',
            'value_as_json',
            'setting_type',
            'setting_type_display',
            'description',
            'is_public',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # ✅ CamelCase aliases
            'isPublic',
            'settingType',
            'createdAt',
            'updatedAt',
            'deletedAt',
            'settingTypeDisplay',
            'valueParsed',
            'valueAsBool',
            'valueAsInt',
            'valueAsFloat',
            'valueAsJson',
        ]
        read_only_fields = ['__all__']

    def get_value_parsed(self, obj):
        """Get parsed value (auto-detects type)."""
        try:
            # Try to parse as JSON
            parsed = json.loads(obj.value)
            return parsed
        except (json.JSONDecodeError, TypeError):
            # Return as string
            return obj.value

    def get_value_as_bool(self, obj):
        return obj.value_as_bool

    def get_value_as_int(self, obj):
        return obj.value_as_int

    def get_value_as_float(self, obj):
        return obj.value_as_float

    def get_value_as_json(self, obj):
        return obj.value_as_json

    # CamelCase method fields
    def get_valueParsed(self, obj):
        try:
            parsed = json.loads(obj.value)
            return parsed
        except (json.JSONDecodeError, TypeError):
            return obj.value

    def get_valueAsBool(self, obj):
        return obj.value_as_bool

    def get_valueAsInt(self, obj):
        return obj.value_as_int

    def get_valueAsFloat(self, obj):
        return obj.value_as_float

    def get_valueAsJson(self, obj):
        return obj.value_as_json


class SystemSettingListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for system setting list views.
    """
    
    setting_type_display = serializers.CharField(source='get_setting_type_display', read_only=True)

    # ✅ CamelCase fields for frontend compatibility
    isPublic = serializers.BooleanField(source='is_public', read_only=True)
    settingType = serializers.CharField(source='setting_type', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    settingTypeDisplay = serializers.CharField(source='get_setting_type_display', read_only=True)

    class Meta:
        model = SystemSetting
        fields = [
            'id',
            'key',
            'value',
            'setting_type',
            'setting_type_display',
            'description',
            'is_public',
            'created_at',
            'updated_at',
            # ✅ CamelCase aliases
            'isPublic',
            'settingType',
            'createdAt',
            'updatedAt',
            'settingTypeDisplay',
        ]
        read_only_fields = ['__all__']


class SystemSettingCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new system setting.
    """
    
    key = serializers.CharField(
        required=True,
        max_length=100,
        help_text="Setting key (e.g., 'company_name', 'default_interest_rate')"
    )
    value = serializers.CharField(
        required=True,
        help_text="Setting value (stored as text, can be JSON string for complex data)"
    )
    setting_type = serializers.ChoiceField(
        choices=SettingType.choices,
        required=True,
        help_text="Category of the setting"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of what this setting does"
    )
    is_public = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether this setting is publicly readable"
    )
    
    class Meta:
        model = SystemSetting
        fields = [
            'key',
            'value',
            'setting_type',
            'description',
            'is_public',
        ]
    
    def validate_key(self, value):
        """Validate key format."""
        if not value or not value.strip():
            raise serializers.ValidationError("Key is required.")
        return value.strip().lower()
    
    def validate_setting_type(self, value):
        """Validate setting type exists."""
        valid_types = [choice[0] for choice in SettingType.choices]
        if value not in valid_types:
            raise serializers.ValidationError(
                f"Invalid setting type. Must be one of: {', '.join(valid_types)}"
            )
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        # Check if setting already exists
        key = data.get('key')
        setting_type = data.get('setting_type')
        
        if key and setting_type:
            existing = SystemSetting.objects.filter(
                key=key,
                setting_type=setting_type,
                deleted_at__isnull=True
            ).exists()
            
            if existing:
                raise serializers.ValidationError({
                    'key': f'Setting with key "{key}" and type "{setting_type}" already exists.'
                })
        
        # Validate JSON value if it looks like JSON
        value = data.get('value')
        if value and isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith('{') or stripped.startswith('['):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    raise serializers.ValidationError({
                        'value': 'Invalid JSON format for complex value.'
                    })
        
        return data
    
    def create(self, validated_data):
        """Create a new system setting."""
        return SystemSetting.objects.create(**validated_data)


class SystemSettingUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing system setting.
    """
    
    key = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Setting key (e.g., 'company_name', 'default_interest_rate')"
    )
    value = serializers.CharField(
        required=False,
        help_text="Setting value (stored as text, can be JSON string for complex data)"
    )
    setting_type = serializers.ChoiceField(
        choices=SettingType.choices,
        required=False,
        help_text="Category of the setting"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of what this setting does"
    )
    is_public = serializers.BooleanField(
        required=False,
        help_text="Whether this setting is publicly readable"
    )
    
    class Meta:
        model = SystemSetting
        fields = [
            'key',
            'value',
            'setting_type',
            'description',
            'is_public',
        ]
        extra_kwargs = {
            'key': {'required': False},
            'value': {'required': False},
            'setting_type': {'required': False},
            'description': {'required': False, 'allow_blank': True, 'allow_null': True},
            'is_public': {'required': False},
        }
    
    def validate_key(self, value):
        """Validate key format."""
        if value and not value.strip():
            raise serializers.ValidationError("Key cannot be empty.")
        return value.strip().lower() if value else value
    
    def validate_setting_type(self, value):
        """Validate setting type exists."""
        if value:
            valid_types = [choice[0] for choice in SettingType.choices]
            if value not in valid_types:
                raise serializers.ValidationError(
                    f"Invalid setting type. Must be one of: {', '.join(valid_types)}"
                )
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance
        
        # If key or setting_type is changing, check for duplicates
        if data.get('key') or data.get('setting_type'):
            new_key = data.get('key', instance.key)
            new_type = data.get('setting_type', instance.setting_type)
            
            existing = SystemSetting.objects.filter(
                key=new_key,
                setting_type=new_type,
                deleted_at__isnull=True
            ).exclude(id=instance.id)
            
            if existing.exists():
                raise serializers.ValidationError({
                    'key': f'Setting with key "{new_key}" and type "{new_type}" already exists.'
                })
        
        # Validate JSON value if it looks like JSON
        value = data.get('value')
        if value and isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith('{') or stripped.startswith('['):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    raise serializers.ValidationError({
                        'value': 'Invalid JSON format for complex value.'
                    })
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing system setting."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class SystemSettingBulkUpdateSerializer(serializers.Serializer):
    """
    Serializer for bulk updating system settings.
    Accepts a list of settings to create or update.
    """
    
    settings = serializers.ListField(
        child=serializers.DictField(),
        required=True,
        help_text="List of settings to create or update"
    )
    
    def validate_settings(self, value):
        """Validate each setting in the list."""
        if not value:
            raise serializers.ValidationError("At least one setting must be provided.")
        
        for idx, setting in enumerate(value):
            # Each setting must have a key
            if not setting.get('key'):
                raise serializers.ValidationError({
                    f'settings[{idx}]': 'Key is required for each setting.'
                })
            
            # Each setting must have a value
            if 'value' not in setting:
                raise serializers.ValidationError({
                    f'settings[{idx}]': 'Value is required for each setting.'
                })
            
            # Setting type is optional, default to GENERAL
            if not setting.get('setting_type'):
                setting['setting_type'] = SettingType.GENERAL
            
            # Validate setting type
            valid_types = [choice[0] for choice in SettingType.choices]
            if setting['setting_type'] not in valid_types:
                raise serializers.ValidationError({
                    f'settings[{idx}]': f"Invalid setting type. Must be one of: {', '.join(valid_types)}"
                })
        
        return value
    
    def save(self, **kwargs):
        """
        Bulk create or update settings.
        """
        settings_data = self.validated_data['settings']
        results = {'created': [], 'updated': [], 'errors': []}
        
        for setting_data in settings_data:
            try:
                key = setting_data['key']
                value = setting_data['value']
                setting_type = setting_data.get('setting_type', SettingType.GENERAL)
                description = setting_data.get('description')
                is_public = setting_data.get('is_public', False)
                
                # Check if setting exists
                existing = SystemSetting.objects.filter(
                    key=key,
                    setting_type=setting_type,
                    deleted_at__isnull=True
                ).first()
                
                if existing:
                    # Update existing
                    old_value = existing.value
                    existing.value = value
                    if description is not None:
                        existing.description = description
                    if 'is_public' in setting_data:
                        existing.is_public = is_public
                    existing.save()
                    results['updated'].append({
                        'key': key,
                        'setting_type': setting_type,
                        'old_value': old_value,
                        'new_value': value
                    })
                else:
                    # Create new
                    setting = SystemSetting.objects.create(
                        key=key,
                        value=value,
                        setting_type=setting_type,
                        description=description,
                        is_public=is_public
                    )
                    results['created'].append({
                        'id': setting.id,
                        'key': key,
                        'setting_type': setting_type
                    })
                    
            except Exception as e:
                results['errors'].append({
                    'key': setting_data.get('key'),
                    'error': str(e)
                })
        
        return results