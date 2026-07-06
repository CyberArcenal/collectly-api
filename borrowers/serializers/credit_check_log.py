from rest_framework import serializers
from django.core.validators import MinValueValidator, MaxValueValidator

from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.models.borrower import Borrower
from borrowers.services.credit_check import CreditCheckService
from users.models import User
from users.serializers.User.nested import UserMinimalSerializer


class CreditCheckLogReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for credit check log detail view.
    """
    
    debtor_name = serializers.CharField(source='debtor.name', read_only=True)
    debtor_email = serializers.CharField(source='debtor.email', read_only=True)
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)
    is_passing = serializers.SerializerMethodField()
    is_excellent = serializers.SerializerMethodField()
    performed_by_data = UserMinimalSerializer(source='performed_by', read_only=True)

    # ✅ CamelCase fields for frontend compatibility
    debtorId = serializers.IntegerField(source='debtor.id', read_only=True)
    debtorName = serializers.CharField(source='debtor.name', read_only=True)
    riskLevel = serializers.CharField(source='risk_level', read_only=True)
    dateChecked = serializers.DateTimeField(source='date_checked', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = CreditCheckLog
        fields = [
            'id',
            'debtor',
            'debtor_name',
            'debtor_email',
            'score',
            'risk_level',
            'risk_level_display',
            'remarks',
            'date_checked',
            'performed_by',
            'performed_by_data',
            'external_reference',
            'is_passing',
            'is_excellent',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # ✅ Added camelCase aliases
            'debtorId',
            'debtorName',
            'riskLevel',
            'dateChecked',
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_is_passing(self, obj):
        return obj.is_passing

    def get_is_excellent(self, obj):
        return obj.is_excellent


class CreditCheckLogListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for credit check log list views.
    """
    
    debtor_name = serializers.CharField(source='debtor.name', read_only=True)
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)

    # ✅ CamelCase fields for frontend compatibility
    debtorId = serializers.IntegerField(source='debtor.id', read_only=True)
    debtorName = serializers.CharField(source='debtor.name', read_only=True)
    riskLevel = serializers.CharField(source='risk_level', read_only=True)
    dateChecked = serializers.DateTimeField(source='date_checked', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = CreditCheckLog
        fields = [
            'id',
            'debtor',
            'debtor_name',
            'score',
            'risk_level',
            'risk_level_display',
            'date_checked',
            'created_at',
            # ✅ Added camelCase aliases
            'debtorId',
            'debtorName',
            'riskLevel',
            'dateChecked',
            'createdAt',
        ]
        read_only_fields = ['__all__']


class CreditCheckLogCreateSerializer(serializers.ModelSerializer):
    debtor_id = serializers.PrimaryKeyRelatedField(
        source='debtor',
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=True,
    )
    score = serializers.IntegerField(
        required=False,  # ← MAKE OPTIONAL
        validators=[MinValueValidator(300), MaxValueValidator(850)],
        help_text="Credit score (auto-computed if not provided)"
    )
    risk_level = serializers.ChoiceField(
        choices=CreditCheckLog.RiskLevel.choices,
        required=False,
        help_text="Risk level (auto-computed if not provided)"
    )
    performed_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
    )
    
    class Meta:
        model = CreditCheckLog
        fields = [
            'debtor_id',
            'score',
            'risk_level',
            'remarks',
            'performed_by',
            'external_reference',
        ]
    
    def validate(self, data):
        """If score not provided, compute it."""
        if 'score' not in data:
            # Compute using service
            borrower_id = data['debtor'].id
            computed = CreditCheckService.compute_score(borrower_id)
            data['score'] = computed['score']
            if 'risk_level' not in data:
                data['risk_level'] = computed['risk_level']
            if 'remarks' not in data and computed.get('remarks'):
                data['remarks'] = computed['remarks']
        return data
    
    def create(self, validated_data):
        return CreditCheckLog.objects.create(**validated_data)


class CreditCheckLogUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing credit check log.
    """
    
    debtor = serializers.PrimaryKeyRelatedField(
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=False,
        help_text="Borrower being checked"
    )
    score = serializers.IntegerField(
        required=False,
        validators=[
            MinValueValidator(300, message="Score must be at least 300"),
            MaxValueValidator(850, message="Score must be at most 850")
        ],
        help_text="Credit score (300-850 range)"
    )
    risk_level = serializers.ChoiceField(
        choices=CreditCheckLog.RiskLevel.choices,
        required=False,
        help_text="Risk level"
    )
    performed_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="User who performed the credit check"
    )
    
    class Meta:
        model = CreditCheckLog
        fields = [
            'debtor',
            'score',
            'risk_level',
            'remarks',
            'performed_by',
            'external_reference',
        ]
        extra_kwargs = {
            'debtor': {'required': False},
            'score': {'required': False},
            'remarks': {'required': False},
            'external_reference': {'required': False},
        }
    
    def validate_score(self, value):
        """Validate score range."""
        if value and (value < 300 or value > 850):
            raise serializers.ValidationError("Score must be between 300 and 850.")
        return value
    
    def update(self, instance, validated_data):
        """Update an existing credit check log."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        # If risk_level is not provided, it will be auto-calculated on save
        instance.save()
        return instance