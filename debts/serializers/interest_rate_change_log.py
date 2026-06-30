from rest_framework import serializers

from debts.models.interest_rate_change_log import InterestRateChangeLog
from debts.models.debt import Debt


class InterestRateChangeLogReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for interest rate change log detail view.
    """
    
    loan_name = serializers.CharField(source='loan.name', read_only=True, allow_null=True)
    change_direction = serializers.SerializerMethodField()
    is_system_change = serializers.SerializerMethodField()
    is_loan_change = serializers.SerializerMethodField()
    
    class Meta:
        model = InterestRateChangeLog
        fields = [
            'id',
            'setting_key',
            'old_value',
            'new_value',
            'changed_by',
            'reason',
            'changed_at',
            'loan',
            'loan_name',
            'change_direction',
            'is_system_change',
            'is_loan_change',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
        ]
        read_only_fields = ['__all__']
    
    def get_change_direction(self, obj):
        return obj.change_direction
    
    def get_is_system_change(self, obj):
        return obj.is_system_change
    
    def get_is_loan_change(self, obj):
        return obj.is_loan_change


class InterestRateChangeLogListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for interest rate change log list views.
    """
    
    change_direction = serializers.SerializerMethodField()
    is_system_change = serializers.SerializerMethodField()
    
    class Meta:
        model = InterestRateChangeLog
        fields = [
            'id',
            'setting_key',
            'old_value',
            'new_value',
            'changed_by',
            'changed_at',
            'loan',
            'change_direction',
            'is_system_change',
        ]
        read_only_fields = ['__all__']
    
    def get_change_direction(self, obj):
        return obj.change_direction
    
    def get_is_system_change(self, obj):
        return obj.is_system_change


class InterestRateChangeLogCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating an interest rate change log.
    """
    
    setting_key = serializers.CharField(
        required=True,
        max_length=100,
        help_text="Which rate was changed (e.g., 'default_interest_rate' or 'loan_123')"
    )
    old_value = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Previous rate value"
    )
    new_value = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="New rate value"
    )
    changed_by = serializers.CharField(
        required=False,
        default='system',
        max_length=255,
        help_text="User who changed the rate"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for the change"
    )
    loan_id = serializers.PrimaryKeyRelatedField(
        source='loan',
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="Specific loan this change applies to (null = system-wide)"
    )
    
    class Meta:
        model = InterestRateChangeLog
        fields = [
            'setting_key',
            'old_value',
            'new_value',
            'changed_by',
            'reason',
            'loan_id',
        ]
    
    def validate(self, data):
        """Cross-field validation."""
        # Ensure setting_key is consistent with loan reference
        if data.get('loan') and not data.get('setting_key', '').startswith('loan_'):
            data['setting_key'] = f"loan_{data['loan'].id}"
        
        # Ensure old_value and new_value are not both null
        if data.get('old_value') is None and data.get('new_value') is None:
            raise serializers.ValidationError({
                'new_value': 'At least one value must be provided.'
            })
        
        return data
    
    def create(self, validated_data):
        """Create a new interest rate change log."""
        return InterestRateChangeLog.objects.create(**validated_data)


class InterestRateChangeLogUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing interest rate change log.
    """
    
    setting_key = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Which rate was changed"
    )
    old_value = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Previous rate value"
    )
    new_value = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="New rate value"
    )
    changed_by = serializers.CharField(
        required=False,
        max_length=255,
        help_text="User who changed the rate"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for the change"
    )
    loan = serializers.PrimaryKeyRelatedField(
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="Specific loan this change applies to"
    )
    
    class Meta:
        model = InterestRateChangeLog
        fields = [
            'setting_key',
            'old_value',
            'new_value',
            'changed_by',
            'reason',
            'loan',
        ]
        extra_kwargs = {
            'setting_key': {'required': False},
            'old_value': {'required': False, 'allow_null': True},
            'new_value': {'required': False, 'allow_null': True},
            'changed_by': {'required': False},
            'reason': {'required': False, 'allow_blank': True, 'allow_null': True},
            'loan': {'required': False, 'allow_null': True},
        }
    
    def validate(self, data):
        """Cross-field validation."""
        # If loan is set, auto-update setting_key
        if data.get('loan'):
            data['setting_key'] = f"loan_{data['loan'].id}"
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing interest rate change log."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance