from rest_framework import serializers
from django.utils import timezone
from decimal import Decimal

from debts.models.debt import Debt
from borrowers.models.borrower import Borrower
from borrowers.serializers.borrower import BorrowerMinimalSerializer


# ---------- Minimal (used as nested relation) ----------
class DebtMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for debt references."""
    borrower = BorrowerMinimalSerializer(read_only=True)
    class Meta:
        model = Debt
        fields = ['id', 'name', 'total_amount', 'remaining_amount', 'due_date', 'status', 'borrower']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class DebtListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    # ✅ Overwrite borrower with minimal serializer
    borrower = BorrowerMinimalSerializer(read_only=True)

    amount_display = serializers.SerializerMethodField()
    remaining_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_overdue = serializers.SerializerMethodField()

    # CamelCase aliases (keep if frontend needs them)
    totalAmount = serializers.DecimalField(source='total_amount', max_digits=12, decimal_places=2, read_only=True)
    remainingAmount = serializers.DecimalField(source='remaining_amount', max_digits=12, decimal_places=2, read_only=True)
    dueDate = serializers.DateField(source='due_date', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    amountDisplay = serializers.SerializerMethodField()
    remainingDisplay = serializers.SerializerMethodField()
    isOverdue = serializers.SerializerMethodField()

    class Meta:
        model = Debt
        fields = [
            'id',
            'borrower',          # nested minimal
            'name',
            'total_amount',
            'remaining_amount',
            'amount_display',
            'remaining_display',
            'due_date',
            'status',
            'status_display',
            'is_overdue',
            'created_at',
            'updated_at',
            # CamelCase aliases
            'totalAmount',
            'remainingAmount',
            'dueDate',
            'createdAt',
            'updatedAt',
            'amountDisplay',
            'remainingDisplay',
            'isOverdue',
        ]
        read_only_fields = ['__all__']

    def get_amount_display(self, obj):
        return obj.amount_display

    def get_remaining_display(self, obj):
        return obj.remaining_display

    def get_is_overdue(self, obj):
        return obj.is_overdue

    # CamelCase getters
    def get_amountDisplay(self, obj):
        return obj.amount_display

    def get_remainingDisplay(self, obj):
        return obj.remaining_display

    def get_isOverdue(self, obj):
        return obj.is_overdue


# ---------- Read (full detail) ----------
class DebtReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""
    # ✅ Overwrite borrower with minimal serializer
    borrower = BorrowerMinimalSerializer(read_only=True)

    amount_display = serializers.SerializerMethodField()
    remaining_display = serializers.SerializerMethodField()
    paid_percentage = serializers.SerializerMethodField()
    is_fully_paid = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    days_overdue = serializers.SerializerMethodField()
    days_until_due = serializers.SerializerMethodField()
    total_payments = serializers.SerializerMethodField()
    total_penalties = serializers.SerializerMethodField()
    total_penalty_amount = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    interest_period_display = serializers.CharField(source='get_interest_calculation_period_display', read_only=True)

    # CamelCase aliases
    totalAmount = serializers.DecimalField(source='total_amount', max_digits=12, decimal_places=2, read_only=True)
    paidAmount = serializers.DecimalField(source='paid_amount', max_digits=12, decimal_places=2, read_only=True)
    remainingAmount = serializers.DecimalField(source='remaining_amount', max_digits=12, decimal_places=2, read_only=True)
    dueDate = serializers.DateField(source='due_date', read_only=True)
    interestCalculationPeriod = serializers.CharField(source='interest_calculation_period', read_only=True)
    interestRate = serializers.DecimalField(source='interest_rate', max_digits=5, decimal_places=2, read_only=True)
    penaltyRate = serializers.DecimalField(source='penalty_rate', max_digits=5, decimal_places=2, read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)
    amountDisplay = serializers.SerializerMethodField()
    remainingDisplay = serializers.SerializerMethodField()
    isOverdue = serializers.SerializerMethodField()

    class Meta:
        model = Debt
        fields = [
            'id',
            'borrower',
            'name',
            'total_amount',
            'paid_amount',
            'remaining_amount',
            'due_date',
            'status',
            'status_display',
            'interest_rate',
            'penalty_rate',
            'interest_calculation_period',
            'interest_period_display',
            'last_interest_accrual_date',
            'amount_display',
            'remaining_display',
            'paid_percentage',
            'is_fully_paid',
            'is_overdue',
            'days_overdue',
            'days_until_due',
            'total_payments',
            'total_penalties',
            'total_penalty_amount',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # CamelCase aliases
            'totalAmount',
            'paidAmount',
            'remainingAmount',
            'dueDate',
            'interestCalculationPeriod',
            'interestRate',
            'penaltyRate',
            'createdAt',
            'updatedAt',
            'deletedAt',
            'amountDisplay',
            'remainingDisplay',
            'isOverdue',
        ]
        read_only_fields = ['__all__']

    def get_amount_display(self, obj):
        return obj.amount_display

    def get_remaining_display(self, obj):
        return obj.remaining_display

    def get_paid_percentage(self, obj):
        return obj.paid_percentage

    def get_is_fully_paid(self, obj):
        return obj.is_fully_paid

    def get_is_overdue(self, obj):
        return obj.is_overdue

    def get_days_overdue(self, obj):
        return obj.days_overdue

    def get_days_until_due(self, obj):
        return obj.days_until_due

    def get_total_payments(self, obj):
        return obj.total_payments

    def get_total_penalties(self, obj):
        return obj.total_penalties

    def get_total_penalty_amount(self, obj):
        return obj.total_penalty_amount

    # CamelCase getters
    def get_amountDisplay(self, obj):
        return obj.amount_display

    def get_remainingDisplay(self, obj):
        return obj.remaining_display

    def get_isOverdue(self, obj):
        return obj.is_overdue


# ---------- Create / Update (completely unchanged) ----------


class DebtCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new debt.
    """
    
    borrower_id = serializers.PrimaryKeyRelatedField(
        source='borrower',
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the borrower"
    )
    total_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        help_text="Total amount of the debt"
    )
    due_date = serializers.DateField(
        required=True,
        help_text="Due date of the debt"
    )
    status = serializers.ChoiceField(
        choices=Debt.Status.choices,
        required=False,
        default=Debt.Status.ACTIVE,
        help_text="Status of the debt"
    )
    interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Interest rate (auto-uses default if not provided)"
    )
    penalty_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Penalty rate (auto-uses default if not provided)"
    )
    interest_calculation_period = serializers.ChoiceField(
        choices=Debt.InterestPeriod.choices,
        required=False,
        default=Debt.InterestPeriod.PER_ANNUM,
        help_text="How interest is calculated"
    )
    
    class Meta:
        model = Debt
        fields = [
            'borrower_id',
            'name',
            'total_amount',
            'paid_amount',
            'due_date',
            'status',
            'interest_rate',
            'penalty_rate',
            'interest_calculation_period',
            'last_interest_accrual_date',
        ]
        extra_kwargs = {
            'paid_amount': {
                'required': False,
                'default': Decimal('0.00'),
                'help_text': 'Amount already paid (default: 0)'
            },
            'last_interest_accrual_date': {
                'required': False,
                'allow_null': True,
            },
        }
    
    def validate_total_amount(self, value):
        """Validate total amount is positive."""
        if value <= 0:
            raise serializers.ValidationError("Total amount must be greater than 0.")
        return value
    
    def validate_due_date(self, value):
        """Validate due date is not in the past (unless status is paid)."""
        # This is a business rule, can be adjusted
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        # Ensure paid_amount doesn't exceed total_amount
        paid_amount = data.get('paid_amount', Decimal('0.00'))
        total_amount = data.get('total_amount', Decimal('0'))
        
        if paid_amount > total_amount:
            raise serializers.ValidationError({
                'paid_amount': 'Paid amount cannot exceed total amount.'
            })
        
        return data


class DebtUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing debt.
    """
    
    borrower_id = serializers.PrimaryKeyRelatedField(
        source='borrower',
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=False,
        help_text="ID of the borrower"
    )
    status = serializers.ChoiceField(
        choices=Debt.Status.choices,
        required=False,
        help_text="Status of the debt"
    )
    
    class Meta:
        model = Debt
        fields = [
            'borrower_id',
            'name',
            'total_amount',
            'paid_amount',
            'due_date',
            'status',
            'interest_rate',
            'penalty_rate',
            'interest_calculation_period',
            'last_interest_accrual_date',
        ]
        extra_kwargs = {
            'name': {'required': False},
            'total_amount': {'required': False},
            'paid_amount': {'required': False},
            'due_date': {'required': False},
            'interest_rate': {'required': False, 'allow_null': True},
            'penalty_rate': {'required': False, 'allow_null': True},
            'interest_calculation_period': {'required': False},
            'last_interest_accrual_date': {'required': False, 'allow_null': True},
        }
    
    def validate_total_amount(self, value):
        """Validate total amount is positive."""
        if value and value <= 0:
            raise serializers.ValidationError("Total amount must be greater than 0.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        # If total_amount is updated, ensure paid_amount doesn't exceed it
        if 'total_amount' in data and 'paid_amount' in data:
            if data['paid_amount'] > data['total_amount']:
                raise serializers.ValidationError({
                    'paid_amount': 'Paid amount cannot exceed total amount.'
                })
        
        # If only total_amount is updated, use existing paid_amount
        if 'total_amount' in data and 'paid_amount' not in data:
            if self.instance and data['total_amount'] < self.instance.paid_amount:
                raise serializers.ValidationError({
                    'total_amount': f'Total amount cannot be less than paid amount (₱{self.instance.paid_amount:,.2f}).'
                })
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing debt."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        # Save will auto-calculate remaining_amount
        instance.save()
        return instance