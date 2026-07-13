from rest_framework import serializers
from django.utils import timezone

from loan_agreements.models.loan_agreement import LoanAgreement
from debts.models.debt import Debt
from debts.serializers.debt import DebtMinimalSerializer
from borrowers.serializers.borrower import BorrowerMinimalSerializer


# ---------- Minimal (used as nested relation) ----------
class LoanAgreementMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for loan agreement references."""
    class Meta:
        model = LoanAgreement
        fields = ['id', 'status', 'agreement_date', 'signed_at']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class LoanAgreementListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    # ✅ Overwrite foreign keys with minimal serializers
    debt = DebtMinimalSerializer(read_only=True)
    borrower = BorrowerMinimalSerializer(read_only=True)

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_signed = serializers.SerializerMethodField()
    has_file = serializers.SerializerMethodField()

    # CamelCase aliases for non‑relation fields
    agreementDate = serializers.DateField(source='agreement_date', read_only=True)
    lenderName = serializers.CharField(source='lender_name', read_only=True)
    signedAt = serializers.DateTimeField(source='signed_at', read_only=True)
    signedBy = serializers.CharField(source='signed_by', read_only=True)
    principalAmount = serializers.DecimalField(
        source='principal_amount', max_digits=12, decimal_places=2, read_only=True
    )
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    isSigned = serializers.SerializerMethodField()
    hasFile = serializers.SerializerMethodField()

    class Meta:
        model = LoanAgreement
        fields = [
            'id',
            'debt',          # nested minimal
            'borrower',      # nested minimal
            'status',
            'status_display',
            'agreement_date',
            'principal_amount',
            'lender_name',
            'signed_at',
            'signed_by',
            'is_signed',
            'has_file',
            'created_at',
            # CamelCase aliases
            'agreementDate',
            'principalAmount',
            'lenderName',
            'signedAt',
            'signedBy',
            'createdAt',
            'isSigned',
            'hasFile',
        ]
        read_only_fields = ['__all__']

    def get_is_signed(self, obj):
        return obj.is_signed

    def get_has_file(self, obj):
        return obj.has_file

    def get_isSigned(self, obj):
        return obj.is_signed

    def get_hasFile(self, obj):
        return obj.has_file


# ---------- Read (full detail) ----------
class LoanAgreementReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""
    # ✅ Overwrite foreign keys with minimal serializers
    debt = DebtMinimalSerializer(read_only=True)
    borrower = BorrowerMinimalSerializer(read_only=True)

    is_signed = serializers.SerializerMethodField()
    is_draft = serializers.SerializerMethodField()
    has_file = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    # CamelCase aliases for non‑relation fields
    agreementDate = serializers.DateField(source='agreement_date', read_only=True)
    lenderName = serializers.CharField(source='lender_name', read_only=True)
    termsText = serializers.CharField(source='terms_text', read_only=True)
    signedAt = serializers.DateTimeField(source='signed_at', read_only=True)
    signedBy = serializers.CharField(source='signed_by', read_only=True)
    principalAmount = serializers.DecimalField(
        source='principal_amount', max_digits=12, decimal_places=2, read_only=True
    )
    interestRate = serializers.DecimalField(
        source='interest_rate', max_digits=5, decimal_places=2, read_only=True
    )
    penaltyRate = serializers.DecimalField(
        source='penalty_rate', max_digits=5, decimal_places=2, read_only=True
    )
    dueDate = serializers.DateField(source='due_date', read_only=True)
    loanStartDate = serializers.DateField(source='loan_start_date', read_only=True)
    anniversaryDay = serializers.IntegerField(source='anniversary_day', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)
    isSigned = serializers.SerializerMethodField()
    isDraft = serializers.SerializerMethodField()
    hasFile = serializers.SerializerMethodField()

    class Meta:
        model = LoanAgreement
        fields = [
            'id',
            'debt',
            'borrower',
            'status',
            'status_display',
            'agreement_date',
            'lender_name',
            'terms_text',
            'file',
            'signed_at',
            'signed_by',
            'principal_amount',
            'interest_rate',
            'penalty_rate',
            'due_date',
            'purpose',
            'loan_start_date',
            'anniversary_day',
            'is_signed',
            'is_draft',
            'has_file',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # CamelCase aliases
            'agreementDate',
            'lenderName',
            'termsText',
            'signedAt',
            'signedBy',
            'principalAmount',
            'interestRate',
            'penaltyRate',
            'dueDate',
            'loanStartDate',
            'anniversaryDay',
            'createdAt',
            'updatedAt',
            'deletedAt',
            'isSigned',
            'isDraft',
            'hasFile',
        ]
        read_only_fields = ['__all__']

    def get_is_signed(self, obj):
        return obj.is_signed

    def get_is_draft(self, obj):
        return obj.is_draft

    def get_has_file(self, obj):
        return obj.has_file

    def get_isSigned(self, obj):
        return obj.is_signed

    def get_isDraft(self, obj):
        return obj.is_draft

    def get_hasFile(self, obj):
        return obj.has_file


# ---------- Create / Update / Sign (completely unchanged) ----------
class LoanAgreementCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new loan agreement.
    """

    debt_id = serializers.PrimaryKeyRelatedField(
        source="debt",
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the debt this agreement belongs to",
    )
    status = serializers.ChoiceField(
        choices=LoanAgreement.Status.choices,
        required=False,
        default=LoanAgreement.Status.DRAFT,
        help_text="Agreement status (draft or signed)",
    )
    file = serializers.FileField(
        required=False, allow_null=True, help_text="Uploaded agreement file (PDF/DOCX)"
    )

    # Snapshot fields (optional, auto-filled if not provided)
    principal_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Principal amount (auto-copied from debt if not provided)",
    )
    interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Interest rate (auto-copied from debt if not provided)",
    )
    penalty_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Penalty rate (auto-copied from debt if not provided)",
    )
    due_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Due date (auto-copied from debt if not provided)",
    )
    loan_start_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Loan start date (auto-copied from debt.created_at if not provided)",
    )
    anniversary_day = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=31,
        help_text="Day of month for anniversary (auto-copied from debt.created_at if not provided)",
    )

    class Meta:
        model = LoanAgreement
        fields = [
            "debt_id",
            "status",
            "agreement_date",
            "lender_name",
            "terms_text",
            "file",
            "principal_amount",
            "interest_rate",
            "penalty_rate",
            "due_date",
            "purpose",
            "loan_start_date",
            "anniversary_day",
        ]

    def validate(self, data):
        """
        Cross-field validation.
        """
        debt = data.get("debt")
        status = data.get("status", LoanAgreement.Status.DRAFT)

        # If status is 'signed', ensure signed_at and signed_by are set
        if status == LoanAgreement.Status.SIGNED:
            if not data.get("signed_at"):
                data["signed_at"] = timezone.now()
            if not data.get("signed_by"):
                raise serializers.ValidationError(
                    {"signed_by": "Signed by is required when signing the agreement."}
                )

        # Check if there's already a signed agreement for this debt
        if debt and status == LoanAgreement.Status.SIGNED:
            existing_signed = LoanAgreement.objects.filter(
                debt=debt, status=LoanAgreement.Status.SIGNED, deleted_at__isnull=True
            ).exists()

            if existing_signed:
                raise serializers.ValidationError(
                    {"debt_id": "This debt already has a signed agreement."}
                )

        # Auto-fill snapshot fields from debt if not provided
        if debt:
            if not data.get("principal_amount"):
                data["principal_amount"] = debt.total_amount
            if not data.get("interest_rate"):
                data["interest_rate"] = debt.interest_rate
            if not data.get("penalty_rate"):
                data["penalty_rate"] = debt.penalty_rate
            if not data.get("due_date"):
                data["due_date"] = debt.due_date
            if not data.get("loan_start_date"):
                data["loan_start_date"] = (
                    debt.created_at.date() if debt.created_at else timezone.now().date()
                )
            if not data.get("anniversary_day"):
                data["anniversary_day"] = (
                    debt.created_at.day if debt.created_at else timezone.now().day
                )

        return data

    def create(self, validated_data):
        """Create a new loan agreement."""
        # If status is signed, set signed_at
        if validated_data.get("status") == LoanAgreement.Status.SIGNED:
            validated_data["signed_at"] = timezone.now()

        return LoanAgreement.objects.create(**validated_data)


class LoanAgreementUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing loan agreement.
    Only allowed for draft agreements.
    """

    debt = serializers.PrimaryKeyRelatedField(
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        help_text="ID of the debt this agreement belongs to",
    )
    status = serializers.ChoiceField(
        choices=LoanAgreement.Status.choices,
        required=False,
        help_text="Agreement status (draft or signed)",
    )
    file = serializers.FileField(
        required=False, allow_null=True, help_text="Uploaded agreement file (PDF/DOCX)"
    )

    class Meta:
        model = LoanAgreement
        fields = [
            "debt",
            "status",
            "agreement_date",
            "lender_name",
            "terms_text",
            "file",
            "signed_at",
            "signed_by",
            "principal_amount",
            "interest_rate",
            "penalty_rate",
            "due_date",
            "purpose",
            "loan_start_date",
            "anniversary_day",
        ]
        extra_kwargs = {
            "debt": {"required": False},
            "status": {"required": False},
            "agreement_date": {"required": False, "allow_null": True},
            "lender_name": {"required": False, "allow_blank": True, "allow_null": True},
            "terms_text": {"required": False, "allow_blank": True, "allow_null": True},
            "file": {"required": False, "allow_null": True},
            "signed_at": {"required": False, "allow_null": True},
            "signed_by": {"required": False, "allow_blank": True, "allow_null": True},
            "principal_amount": {"required": False, "allow_null": True},
            "interest_rate": {"required": False, "allow_null": True},
            "penalty_rate": {"required": False, "allow_null": True},
            "due_date": {"required": False, "allow_null": True},
            "purpose": {"required": False, "allow_blank": True, "allow_null": True},
            "loan_start_date": {"required": False, "allow_null": True},
            "anniversary_day": {"required": False, "allow_null": True},
        }

    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance

        # Cannot update signed agreements
        if instance and instance.status == LoanAgreement.Status.SIGNED:
            raise serializers.ValidationError(
                "Cannot update a signed agreement. It is immutable."
            )

        # If changing status to signed, validate required fields
        if data.get("status") == LoanAgreement.Status.SIGNED:
            if not data.get("signed_by"):
                if instance and not instance.signed_by:
                    raise serializers.ValidationError(
                        {
                            "signed_by": "Signed by is required when signing the agreement."
                        }
                    )
                elif not data.get("signed_by"):
                    raise serializers.ValidationError(
                        {
                            "signed_by": "Signed by is required when signing the agreement."
                        }
                    )

            if not data.get("signed_at"):
                data["signed_at"] = timezone.now()

        # Check if there's already a signed agreement for this debt (excluding self)
        debt = data.get("debt") or (instance.debt if instance else None)
        if debt and data.get("status") == LoanAgreement.Status.SIGNED:
            existing_signed = LoanAgreement.objects.filter(
                debt=debt, status=LoanAgreement.Status.SIGNED, deleted_at__isnull=True
            )
            if instance:
                existing_signed = existing_signed.exclude(id=instance.id)

            if existing_signed.exists():
                raise serializers.ValidationError(
                    {"debt": "This debt already has a signed agreement."}
                )

        return data

    def update(self, instance, validated_data):
        """Update an existing loan agreement."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class LoanAgreementSignSerializer(serializers.Serializer):
    """
    Serializer for signing a loan agreement.
    """

    signed_by = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Name or ID of the person signing the agreement",
    )

    def validate(self, data):
        """Validate that the agreement is in draft status."""
        instance = self.instance

        if not instance:
            raise serializers.ValidationError({"detail": "Agreement not found."})

        if instance.status == LoanAgreement.Status.SIGNED:
            raise serializers.ValidationError(
                {"detail": "Agreement is already signed."}
            )

        # Check if there's already a signed agreement for this debt
        if LoanAgreement.objects.filter(
            debt=instance.debt,
            status=LoanAgreement.Status.SIGNED,
            deleted_at__isnull=True,
        ).exists():
            raise serializers.ValidationError(
                {"detail": "This debt already has a signed agreement."}
            )

        return data

    def save(self, **kwargs):
        """Sign the agreement."""
        instance = self.instance
        instance.status = LoanAgreement.Status.SIGNED
        instance.signed_at = timezone.now()
        instance.signed_by = self.validated_data["signed_by"]

        # Snapshot debt data at signing time
        debt = instance.debt
        if debt:
            instance.principal_amount = debt.total_amount
            instance.interest_rate = debt.interest_rate
            instance.penalty_rate = debt.penalty_rate
            instance.due_date = debt.due_date

        instance.save()
        return instance