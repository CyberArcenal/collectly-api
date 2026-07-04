from rest_framework import serializers
from django.db import transaction

from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat


class PaymentMethodStatsSerializer(serializers.ModelSerializer):
    """
    Serializer for payment method statistics.
    """
    
    average_transaction = serializers.SerializerMethodField()

    # ✅ CamelCase fields for frontend compatibility
    transactionCount = serializers.IntegerField(source='transaction_count', read_only=True)
    totalAmount = serializers.DecimalField(source='total_amount', max_digits=15, decimal_places=2, read_only=True)
    averageTransaction = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = PaymentMethodStat
        fields = [
            'id',
            'transaction_count',
            'total_amount',
            'average_transaction',
            'created_at',
            'updated_at',
            # ✅ CamelCase aliases
            'transactionCount',
            'totalAmount',
            'averageTransaction',
            'createdAt',
            'updatedAt',
        ]
        read_only_fields = ['__all__']

    def get_average_transaction(self, obj):
        return obj.average_transaction

    def get_averageTransaction(self, obj):
        return obj.average_transaction


class PaymentMethodReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for payment method detail view.
    Includes nested stats data.
    """
    
    stats = PaymentMethodStatsSerializer(read_only=True)
    is_default_display = serializers.SerializerMethodField()

    # ✅ CamelCase fields for frontend compatibility
    isDefault = serializers.BooleanField(source='is_default', read_only=True)
    isDefaultDisplay = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = PaymentMethod
        fields = [
            'id',
            'name',
            'description',
            'icon',
            'is_default',
            'is_default_display',
            'stats',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # ✅ CamelCase aliases
            'isDefault',
            'isDefaultDisplay',
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_is_default_display(self, obj):
        return "Yes" if obj.is_default else "No"

    def get_isDefaultDisplay(self, obj):
        return "Yes" if obj.is_default else "No"


class PaymentMethodListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for payment method list views.
    """
    
    is_default_display = serializers.SerializerMethodField()
    transaction_count = serializers.SerializerMethodField()

    # ✅ CamelCase fields for frontend compatibility
    isDefault = serializers.BooleanField(source='is_default', read_only=True)
    isDefaultDisplay = serializers.SerializerMethodField()
    transactionCount = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = PaymentMethod
        fields = [
            'id',
            'name',
            'description',
            'icon',
            'is_default',
            'is_default_display',
            'transaction_count',
            'created_at',
            # ✅ CamelCase aliases
            'isDefault',
            'isDefaultDisplay',
            'transactionCount',
            'createdAt',
            'updatedAt',
        ]
        read_only_fields = ['__all__']

    def get_is_default_display(self, obj):
        return "Yes" if obj.is_default else "No"

    def get_transaction_count(self, obj):
        if hasattr(obj, 'stats'):
            return obj.stats.transaction_count
        return 0

    def get_isDefaultDisplay(self, obj):
        return "Yes" if obj.is_default else "No"

    def get_transactionCount(self, obj):
        if hasattr(obj, 'stats'):
            return obj.stats.transaction_count
        return 0


class PaymentMethodCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new payment method.
    """
    
    name = serializers.CharField(
        required=True,
        max_length=100,
        help_text="Name of the payment method (must be unique)"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of the payment method"
    )
    icon = serializers.CharField(
        required=False,
        max_length=50,
        default='CreditCard',
        help_text="Icon name (for UI)"
    )
    is_default = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether this is the default payment method"
    )
    
    class Meta:
        model = PaymentMethod
        fields = [
            'name',
            'description',
            'icon',
            'is_default',
        ]
    
    def validate_name(self, value):
        """Validate name uniqueness."""
        if PaymentMethod.objects.filter(name=value).exists():
            raise serializers.ValidationError("Payment method name already exists.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        # If is_default is True, ensure no other default exists
        if data.get('is_default'):
            # This will be handled in the service layer
            pass
        return data
    
    def create(self, validated_data):
        """Create a new payment method."""
        is_default = validated_data.get('is_default', False)
        
        # If this is default, remove other defaults
        if is_default:
            PaymentMethod.objects.filter(is_default=True).update(is_default=False)
        
        method = PaymentMethod.objects.create(**validated_data)
        
        # Create stats record
        PaymentMethodStat.objects.create(method=method)
        
        return method


class PaymentMethodUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing payment method.
    """
    
    name = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Name of the payment method (must be unique)"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of the payment method"
    )
    icon = serializers.CharField(
        required=False,
        max_length=50,
        help_text="Icon name (for UI)"
    )
    is_default = serializers.BooleanField(
        required=False,
        help_text="Whether this is the default payment method"
    )
    
    class Meta:
        model = PaymentMethod
        fields = [
            'name',
            'description',
            'icon',
            'is_default',
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False, 'allow_blank': True, 'allow_null': True},
            'icon': {'required': False},
            'is_default': {'required': False},
        }
    
    def validate_name(self, value):
        """Validate name uniqueness (excluding current instance)."""
        if value:
            existing = PaymentMethod.objects.filter(name=value)
            if self.instance:
                existing = existing.exclude(id=self.instance.id)
            if existing.exists():
                raise serializers.ValidationError("Payment method name already exists.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        instance = self.instance
        
        # If setting is_default to True, ensure no other default exists
        if data.get('is_default') and not instance.is_default:
            # This will be handled in the service layer
            pass
        
        # Prevent removing default if it's the only one
        if data.get('is_default') is False and instance.is_default:
            # Check if there are other payment methods
            other_methods = PaymentMethod.objects.filter(
                deleted_at__isnull=True
            ).exclude(id=instance.id)
            if not other_methods.exists():
                raise serializers.ValidationError({
                    'is_default': 'Cannot remove default status from the only payment method.'
                })
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing payment method."""
        is_default = validated_data.get('is_default')
        
        # If setting as default, remove other defaults
        if is_default and not instance.is_default:
            PaymentMethod.objects.filter(is_default=True).update(is_default=False)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        return instance


class PaymentMethodSetDefaultSerializer(serializers.Serializer):
    """
    Serializer for setting a payment method as default.
    """
    
    confirm = serializers.BooleanField(
        required=True,
        help_text="Confirm to set this payment method as default"
    )
    
    def validate_confirm(self, value):
        """Validate confirmation."""
        if not value:
            raise serializers.ValidationError("Please confirm to set as default.")
        return value
    
    def validate(self, data):
        """Validate that the payment method exists and is not deleted."""
        instance = self.instance
        
        if not instance:
            raise serializers.ValidationError({
                'detail': 'Payment method not found.'
            })
        
        if instance.deleted_at:
            raise serializers.ValidationError({
                'detail': 'Cannot set a deleted payment method as default.'
            })
        
        return data
    
    def save(self, **kwargs):
        """Set the payment method as default."""
        instance = self.instance
        
        # Remove other defaults
        PaymentMethod.objects.filter(is_default=True).update(is_default=False)
        
        instance.is_default = True
        instance.save()
        
        return instance