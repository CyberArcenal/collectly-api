from rest_framework import serializers
from django.utils import timezone
from decimal import Decimal

from payments.models.penalty_transaction import PenaltyTransaction
from debts.models.debt import Debt
from debts.serializers.debt import DebtMinimalSerializer


# ---------- Minimal (used as nested relation) ----------
class PenaltyTransactionMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for penalty transaction references."""
    class Meta:
        model = PenaltyTransaction
        fields = ['id', 'amount', 'penalty_date', 'is_auto']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class PenaltyTransactionListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    # ✅ Overwrite debt field with minimal serializer
    debt = DebtMinimalSerializer(read_only=True)

    amount_display = serializers.SerializerMethodField()
    is_auto_display = serializers.SerializerMethodField()

    # CamelCase aliases for non‑relation fields
    penaltyDate = serializers.DateField(source='penalty_date', read_only=True)
    isAuto = serializers.BooleanField(source='is_auto', read_only=True)
    amountDisplay = serializers.SerializerMethodField()
    isAutoDisplay = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = PenaltyTransaction
        fields = [
            'id',
            'debt',          # nested minimal
            'amount',
            'amount_display',
            'penalty_date',
            'reason',
            'is_auto',
            'is_auto_display',
            'created_at',
            # CamelCase aliases
            'penaltyDate',
            'isAuto',
            'amountDisplay',
            'isAutoDisplay',
            'createdAt',
        ]
        read_only_fields = ['__all__']

    def get_amount_display(self, obj):
        return obj.amount_display

    def get_is_auto_display(self, obj):
        return "Auto" if obj.is_auto else "Manual"

    def get_amountDisplay(self, obj):
        return obj.amount_display

    def get_isAutoDisplay(self, obj):
        return "Auto" if obj.is_auto else "Manual"


# ---------- Read (full detail) ----------
class PenaltyTransactionReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""
    # ✅ Overwrite debt field with minimal serializer
    debt = DebtMinimalSerializer(read_only=True)

    amount_display = serializers.SerializerMethodField()
    is_void = serializers.SerializerMethodField()
    is_auto_display = serializers.SerializerMethodField()

    # CamelCase aliases for non‑relation fields
    penaltyDate = serializers.DateField(source='penalty_date', read_only=True)
    isAuto = serializers.BooleanField(source='is_auto', read_only=True)
    amountDisplay = serializers.SerializerMethodField()
    isVoid = serializers.SerializerMethodField()
    isAutoDisplay = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = PenaltyTransaction
        fields = [
            'id',
            'debt',
            'amount',
            'amount_display',
            'penalty_date',
            'reason',
            'is_auto',
            'is_auto_display',
            'is_void',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # CamelCase aliases
            'penaltyDate',
            'isAuto',
            'amountDisplay',
            'isVoid',
            'isAutoDisplay',
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_amount_display(self, obj):
        return obj.amount_display

    def get_is_void(self, obj):
        return obj.is_void

    def get_is_auto_display(self, obj):
        return "Auto" if obj.is_auto else "Manual"

    def get_amountDisplay(self, obj):
        return obj.amount_display

    def get_isVoid(self, obj):
        return obj.is_void

    def get_isAutoDisplay(self, obj):
        return "Auto" if obj.is_auto else "Manual"


# ---------- Create / Update (completely unchanged) ----------
class PenaltyTransactionCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new penalty transaction.
    """
    
    debt_id = serializers.PrimaryKeyRelatedField(
        source='debt',
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the debt being penalized"
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text="Penalty amount"
    )
    penalty_date = serializers.DateField(
        required=False,
        help_text="Date when penalty was applied (defaults to today)"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for the penalty"
    )
    is_auto = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether this penalty was auto-generated by the system"
    )
    
    class Meta:
        model = PenaltyTransaction
        fields = [
            'debt_id',
            'amount',
            'penalty_date',
            'reason',
            'is_auto',
        ]
    
    def validate_amount(self, value):
        """Validate amount is positive."""
        if value <= 0:
            raise serializers.ValidationError("Penalty amount must be greater than 0.")
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        debt = data.get('debt')
        amount = data.get('amount')
        
        # Validate penalty amount is reasonable (optional)
        if debt and amount:
            # Check if penalty would make remaining amount negative (should not happen)
            # Remaining amount will be updated in service
            pass
        
        # Set penalty_date to today if not provided
        if not data.get('penalty_date'):
            data['penalty_date'] = timezone.now().date()
        
        return data
    
    def create(self, validated_data):
        """Create a new penalty transaction."""
        return PenaltyTransaction.objects.create(**validated_data)


class PenaltyTransactionUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing penalty transaction.
    """
    
    debt = serializers.PrimaryKeyRelatedField(
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        help_text="ID of the debt being penalized"
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Penalty amount"
    )
    penalty_date = serializers.DateField(
        required=False,
        help_text="Date when penalty was applied"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for the penalty"
    )
    is_auto = serializers.BooleanField(
        required=False,
        help_text="Whether this penalty was auto-generated by the system"
    )
    
    class Meta:
        model = PenaltyTransaction
        fields = [
            'debt',
            'amount',
            'penalty_date',
            'reason',
            'is_auto',
        ]
        extra_kwargs = {
            'debt': {'required': False},
            'amount': {'required': False},
            'penalty_date': {'required': False},
            'reason': {'required': False, 'allow_blank': True, 'allow_null': True},
            'is_auto': {'required': False},
        }
    
    def validate_amount(self, value):
        """Validate amount is positive."""
        if value and value <= 0:
            raise serializers.ValidationError("Penalty amount must be greater than 0.")
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance
        
        # Cannot update voided penalties
        if instance and instance.deleted_at:
            raise serializers.ValidationError(
                "Cannot update a voided penalty."
            )
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing penalty transaction."""
        # Note: Updating penalty amount requires service layer to update debt balances
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance