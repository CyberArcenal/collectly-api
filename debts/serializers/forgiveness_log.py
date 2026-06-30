from rest_framework import serializers
from decimal import Decimal

from debts.models.forgiveness_log import ForgivenessLog
from debts.models.debt import Debt
from borrowers.models.borrower import Borrower


class ForgivenessLogReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for forgiveness log detail view.
    """
    
    debt_name = serializers.CharField(source='debt.name', read_only=True)
    debtor_name = serializers.CharField(source='borrower.name', read_only=True)
    amount_display = serializers.SerializerMethodField()
    is_approved = serializers.SerializerMethodField()
    is_pending = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = ForgivenessLog
        fields = [
            'id',
            'debt',
            'debt_name',
            'borrower',
            'debtor_name',
            'amount_forgiven',
            'amount_display',
            'previous_total_amount',
            'new_total_amount',
            'reason',
            'created_by',
            'status',
            'status_display',
            'approved_by',
            'approved_at',
            'is_approved',
            'is_pending',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
        ]
        read_only_fields = ['__all__']
    
    def get_amount_display(self, obj):
        return obj.amount_display
    
    def get_is_approved(self, obj):
        return obj.is_approved
    
    def get_is_pending(self, obj):
        return obj.is_pending


class ForgivenessLogListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for forgiveness log list views.
    """
    
    debt_name = serializers.CharField(source='debt.name', read_only=True)
    debtor_name = serializers.CharField(source='borrower.name', read_only=True)
    amount_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = ForgivenessLog
        fields = [
            'id',
            'debt',
            'debt_name',
            'borrower',
            'debtor_name',
            'amount_forgiven',
            'amount_display',
            'reason',
            'status',
            'status_display',
            'created_by',
            'created_at',
        ]
        read_only_fields = ['__all__']
    
    def get_amount_display(self, obj):
        return obj.amount_display


class ForgivenessLogCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a forgiveness log.
    """
    
    debt_id = serializers.PrimaryKeyRelatedField(
        source='debt',
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the debt being forgiven"
    )
    borrower_id = serializers.PrimaryKeyRelatedField(
        source='borrower',
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the borrower (denormalized for faster queries)"
    )
    amount_forgiven = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text="Amount to forgive"
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for forgiveness"
    )
    created_by = serializers.CharField(
        required=False,
        default='system',
        help_text="User who created the forgiveness request"
    )
    status = serializers.ChoiceField(
        choices=ForgivenessLog.Status.choices,
        required=False,
        default=ForgivenessLog.Status.APPROVED,
        help_text="Approval status"
    )
    
    class Meta:
        model = ForgivenessLog
        fields = [
            'debt_id',
            'borrower_id',
            'amount_forgiven',
            'reason',
            'created_by',
            'status',
        ]
    
    def validate_amount_forgiven(self, value):
        """Validate forgiveness amount."""
        if value <= 0:
            raise serializers.ValidationError("Forgiveness amount must be positive.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        debt = data.get('debt')
        amount_forgiven = data.get('amount_forgiven')
        
        if debt and amount_forgiven:
            if amount_forgiven > debt.total_amount:
                raise serializers.ValidationError({
                    'amount_forgiven': f'Cannot forgive more than total amount (₱{debt.total_amount:,.2f}).'
                })
            
            if debt.remaining_amount <= Decimal('0.01'):
                raise serializers.ValidationError({
                    'amount_forgiven': 'Debt is already fully paid.'
                })
        
        return data
    
    def create(self, validated_data):
        """Create a new forgiveness log."""
        # Store previous total amount
        debt = validated_data.get('debt')
        validated_data['previous_total_amount'] = debt.total_amount
        validated_data['new_total_amount'] = debt.total_amount - validated_data['amount_forgiven']
        
        return ForgivenessLog.objects.create(**validated_data)


class ForgivenessLogUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing forgiveness log.
    """
    
    status = serializers.ChoiceField(
        choices=ForgivenessLog.Status.choices,
        required=False,
        help_text="Approval status"
    )
    approved_by = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="User who approved the forgiveness"
    )
    approved_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="When the forgiveness was approved"
    )
    
    class Meta:
        model = ForgivenessLog
        fields = [
            'reason',
            'status',
            'approved_by',
            'approved_at',
        ]
        extra_kwargs = {
            'reason': {'required': False, 'allow_blank': True, 'allow_null': True},
            'approved_by': {'required': False, 'allow_blank': True, 'allow_null': True},
            'approved_at': {'required': False, 'allow_null': True},
        }
    
    def validate(self, data):
        """Cross-field validation."""
        # If status is changing to approved, ensure approved_by is set
        if data.get('status') == ForgivenessLog.Status.APPROVED and not data.get('approved_by'):
            if not self.instance or not self.instance.approved_by:
                raise serializers.ValidationError({
                    'approved_by': 'Approved by is required when approving forgiveness.'
                })
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing forgiveness log."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance