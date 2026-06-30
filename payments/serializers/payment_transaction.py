from rest_framework import serializers
from django.utils import timezone
from decimal import Decimal

from payments.models.payment_transaction import PaymentTransaction
from debts.models.debt import Debt
from debts.serializers.debt import DebtListSerializer
from payment_methods.models.payment_method import PaymentMethod
from payment_methods.serializers.payment_method import PaymentMethodListSerializer
from users.models import User
from users.serializers.User import UserMinimalSerializer


class PaymentTransactionReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for payment transaction detail view.
    Includes nested debt, method, and recorded_by data.
    """
    
    debt_data = DebtListSerializer(source='debt', read_only=True)
    method_data = PaymentMethodListSerializer(source='method', read_only=True)
    recorded_by_data = UserMinimalSerializer(source='recorded_by', read_only=True)
    amount_display = serializers.SerializerMethodField()
    is_void = serializers.SerializerMethodField()
    payment_method_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PaymentTransaction
        fields = [
            'id',
            'debt',
            'debt_data',
            'method',
            'method_data',
            'payment_method_name',
            'amount',
            'amount_display',
            'payment_date',
            'reference',
            'notes',
            'recorded_at',
            'recorded_by',
            'recorded_by_data',
            'is_void',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
        ]
        read_only_fields = ['__all__']
    
    def get_amount_display(self, obj):
        return obj.amount_display
    
    def get_is_void(self, obj):
        return obj.is_void
    
    def get_payment_method_name(self, obj):
        return obj.payment_method_name


class PaymentTransactionListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for payment transaction list views.
    """
    
    debt_name = serializers.CharField(source='debt.name', read_only=True)
    borrower_name = serializers.CharField(source='debt.borrower.name', read_only=True)
    method_name = serializers.CharField(source='method.name', read_only=True, allow_null=True)
    amount_display = serializers.SerializerMethodField()
    is_void = serializers.SerializerMethodField()
    
    class Meta:
        model = PaymentTransaction
        fields = [
            'id',
            'debt',
            'debt_name',
            'borrower_name',
            'method',
            'method_name',
            'amount',
            'amount_display',
            'payment_date',
            'reference',
            'recorded_at',
            'is_void',
            'created_at',
        ]
        read_only_fields = ['__all__']
    
    def get_amount_display(self, obj):
        return obj.amount_display
    
    def get_is_void(self, obj):
        return obj.is_void


class PaymentTransactionCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new payment transaction.
    """
    
    debt_id = serializers.PrimaryKeyRelatedField(
        source='debt',
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the debt being paid"
    )
    method_id = serializers.PrimaryKeyRelatedField(
        source='method',
        queryset=PaymentMethod.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="ID of the payment method used"
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text="Amount paid"
    )
    payment_date = serializers.DateField(
        required=True,
        help_text="Date when payment was made"
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100,
        help_text="Reference number (e.g., transaction ID, check number)"
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Additional notes about the payment"
    )
    recorded_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="User who recorded the payment"
    )
    
    class Meta:
        model = PaymentTransaction
        fields = [
            'debt_id',
            'method_id',
            'amount',
            'payment_date',
            'reference',
            'notes',
            'recorded_by',
        ]
    
    def validate_amount(self, value):
        """Validate amount is positive."""
        if value <= 0:
            raise serializers.ValidationError("Payment amount must be greater than 0.")
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        debt = data.get('debt')
        amount = data.get('amount')
        payment_date = data.get('payment_date')
        
        # Validate payment amount does not exceed remaining balance
        if debt and amount:
            # Get remaining balance (will be updated after interest accrual in service)
            remaining = debt.remaining_amount
            
            if amount > remaining:
                raise serializers.ValidationError({
                    'amount': f'Payment amount (₱{amount:,.2f}) exceeds remaining balance (₱{remaining:,.2f}).'
                })
            
            if debt.status == Debt.Status.PAID:
                raise serializers.ValidationError({
                    'debt_id': 'Cannot add payment to a fully paid debt.'
                })
        
        # Validate payment date is not in the future
        if payment_date and payment_date > timezone.now().date():
            raise serializers.ValidationError({
                'payment_date': 'Payment date cannot be in the future.'
            })
        
        return data
    
    def create(self, validated_data):
        """Create a new payment transaction."""
        # Set recorded_at to now
        validated_data['recorded_at'] = timezone.now()
        
        # Generate reference if not provided
        if not validated_data.get('reference'):
            validated_data['reference'] = self._generate_reference()
        
        return PaymentTransaction.objects.create(**validated_data)
    
    def _generate_reference(self):
        """Generate unique payment reference."""
        import uuid
        from datetime import datetime
        
        date_part = datetime.now().strftime('%Y%m%d')
        random_part = str(uuid.uuid4())[:8].upper()
        return f"PAY-{date_part}-{random_part}"


class PaymentTransactionUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing payment transaction.
    Only allowed for non-voided payments within edit window.
    """
    
    debt = serializers.PrimaryKeyRelatedField(
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        help_text="ID of the debt being paid"
    )
    method = serializers.PrimaryKeyRelatedField(
        queryset=PaymentMethod.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="ID of the payment method used"
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Amount paid"
    )
    payment_date = serializers.DateField(
        required=False,
        help_text="Date when payment was made"
    )
    reference = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100,
        help_text="Reference number"
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Additional notes about the payment"
    )
    recorded_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="User who recorded the payment"
    )
    
    class Meta:
        model = PaymentTransaction
        fields = [
            'debt',
            'method',
            'amount',
            'payment_date',
            'reference',
            'notes',
            'recorded_by',
        ]
        extra_kwargs = {
            'debt': {'required': False},
            'method': {'required': False, 'allow_null': True},
            'amount': {'required': False},
            'payment_date': {'required': False},
            'reference': {'required': False, 'allow_blank': True, 'allow_null': True},
            'notes': {'required': False, 'allow_blank': True, 'allow_null': True},
            'recorded_by': {'required': False, 'allow_null': True},
        }
    
    def validate_amount(self, value):
        """Validate amount is positive."""
        if value and value <= 0:
            raise serializers.ValidationError("Payment amount must be greater than 0.")
        return value
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance
        
        # Cannot update voided payments
        if instance and instance.deleted_at:
            raise serializers.ValidationError(
                "Cannot update a voided payment."
            )
        
        # Validate payment date is not in the future
        if data.get('payment_date') and data['payment_date'] > timezone.now().date():
            raise serializers.ValidationError({
                'payment_date': 'Payment date cannot be in the future.'
            })
        
        # If debt is changed, validate amount against new debt's remaining balance
        if data.get('debt') and data.get('amount'):
            debt = data['debt']
            amount = data['amount']
            
            if amount > debt.remaining_amount + (instance.amount if instance else 0):
                raise serializers.ValidationError({
                    'amount': f'Payment amount exceeds remaining balance of new debt.'
                })
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing payment transaction."""
        # Note: Updating payment amount/debt requires service layer to update debt balances
        # The service will handle the business logic
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class PaymentTransactionVoidSerializer(serializers.Serializer):
    """
    Serializer for voiding a payment transaction.
    """
    
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Reason for voiding the payment"
    )
    confirm = serializers.BooleanField(
        required=True,
        help_text="Confirm to void this payment"
    )
    
    def validate_confirm(self, value):
        """Validate confirmation."""
        if not value:
            raise serializers.ValidationError("Please confirm to void this payment.")
        return value
    
    def validate(self, data):
        """
        Validate that the payment exists and is not already voided.
        """
        instance = self.instance
        
        if not instance:
            raise serializers.ValidationError({
                'detail': 'Payment not found.'
            })
        
        if instance.deleted_at:
            raise serializers.ValidationError({
                'detail': 'Payment is already voided.'
            })
        
        return data
    
    def save(self, **kwargs):
        """Void the payment."""
        instance = self.instance
        
        # Soft delete the payment
        instance.soft_delete()
        
        # Reverse payment amount from debt
        debt = instance.debt
        if debt:
            debt.paid_amount -= instance.amount
            if debt.paid_amount < 0:
                debt.paid_amount = Decimal('0.00')
            debt.remaining_amount = debt.total_amount - debt.paid_amount
            if debt.remaining_amount < 0:
                debt.remaining_amount = Decimal('0.00')
            debt.save()
        
        # Update payment method stats (decrement)
        if instance.method and hasattr(instance.method, 'stats'):
            instance.method.stats.decrement(instance.amount)
        
        return instance