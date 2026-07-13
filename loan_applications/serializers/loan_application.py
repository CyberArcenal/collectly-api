from rest_framework import serializers
from django.utils import timezone

from loan_applications.models.loan_application import LoanApplication
from borrowers.models.borrower import Borrower
from borrowers.serializers.borrower import BorrowerMinimalSerializer


# ---------- Minimal (used as nested relation) ----------
class LoanApplicationMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for loan application references."""

    class Meta:
        model = LoanApplication
        fields = ["id", "debtor_name", "requested_amount", "status", "created_at"]
        read_only_fields = ["__all__"]


# ---------- List (lightweight) ----------
class LoanApplicationListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""

    # ✅ Overwrite debtor field with minimal serializer
    debtor = BorrowerMinimalSerializer(read_only=True)

    amount_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    # CamelCase aliases for non‑relation fields
    requestedAmount = serializers.DecimalField(
        source="requested_amount", max_digits=12, decimal_places=2, read_only=True
    )
    proposedDueDate = serializers.DateField(source="proposed_due_date", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    amountDisplay = serializers.SerializerMethodField()

    class Meta:
        model = LoanApplication
        fields = [
            "id",
            "debtor",  # nested minimal borrower
            "debtor_name",  # snapshot field (model field)
            "debtor_email",  # snapshot field (model field)
            "requested_amount",
            "amount_display",
            "purpose",
            "proposed_due_date",
            "status",
            "status_display",
            "created_at",
            # CamelCase aliases
            "requestedAmount",
            "proposedDueDate",
            "createdAt",
            "amountDisplay",
        ]
        read_only_fields = ["__all__"]

    def get_amount_display(self, obj):
        return obj.amount_display

    def get_amountDisplay(self, obj):
        return obj.amount_display


# ---------- Read (full detail) ----------
class LoanApplicationReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""

    # ✅ Overwrite debtor field with minimal serializer
    debtor = BorrowerMinimalSerializer(read_only=True)

    is_pending = serializers.SerializerMethodField()
    is_approved = serializers.SerializerMethodField()
    is_rejected = serializers.SerializerMethodField()
    amount_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    # CamelCase aliases for non‑relation fields
    requestedAmount = serializers.DecimalField(
        source="requested_amount", max_digits=12, decimal_places=2, read_only=True
    )
    proposedDueDate = serializers.DateField(source="proposed_due_date", read_only=True)
    interestRate = serializers.DecimalField(
        source="interest_rate", max_digits=5, decimal_places=2, read_only=True
    )
    approvedAt = serializers.DateTimeField(source="approved_at", read_only=True)
    rejectedAt = serializers.DateTimeField(source="rejected_at", read_only=True)
    approvedBy = serializers.CharField(source="approved_by", read_only=True)
    rejectionReason = serializers.CharField(source="rejection_reason", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    deletedAt = serializers.DateTimeField(source="deleted_at", read_only=True)
    amountDisplay = serializers.SerializerMethodField()
    isPending = serializers.SerializerMethodField()
    isApproved = serializers.SerializerMethodField()
    isRejected = serializers.SerializerMethodField()

    class Meta:
        model = LoanApplication
        fields = [
            "id",
            "debtor",  # nested minimal borrower
            "debtor_name",  # snapshot field (model field)
            "debtor_contact",  # snapshot field (model field)
            "debtor_email",  # snapshot field (model field)
            "debtor_address",  # snapshot field (model field)
            "requested_amount",
            "amount_display",
            "purpose",
            "proposed_due_date",
            "interest_rate",
            "status",
            "status_display",
            "approved_at",
            "rejected_at",
            "approved_by",
            "rejection_reason",
            "is_pending",
            "is_approved",
            "is_rejected",
            "created_at",
            "updated_at",
            "deleted_at",
            "is_deleted",
            # CamelCase aliases
            "requestedAmount",
            "proposedDueDate",
            "interestRate",
            "approvedAt",
            "rejectedAt",
            "approvedBy",
            "rejectionReason",
            "createdAt",
            "updatedAt",
            "deletedAt",
            "amountDisplay",
            "isPending",
            "isApproved",
            "isRejected",
        ]
        read_only_fields = ["__all__"]

    def get_is_pending(self, obj):
        return obj.is_pending

    def get_is_approved(self, obj):
        return obj.is_approved

    def get_is_rejected(self, obj):
        return obj.is_rejected

    def get_amount_display(self, obj):
        return obj.amount_display

    # CamelCase getters
    def get_isPending(self, obj):
        return obj.is_pending

    def get_isApproved(self, obj):
        return obj.is_approved

    def get_isRejected(self, obj):
        return obj.is_rejected

    def get_amountDisplay(self, obj):
        return obj.amount_display


# ---------- Create / Update / Approve / Reject (completely unchanged) ----------
class LoanApplicationCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new loan application.
    Supports creating with existing debtor or new debtor data.
    """

    debtor_id = serializers.PrimaryKeyRelatedField(
        source="debtor",
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="ID of existing debtor (optional if new_debtor is provided)",
    )

    # New debtor data (optional)
    new_debtor = serializers.DictField(
        required=False,
        write_only=True,
        help_text="Data for creating a new debtor: {'name', 'contact', 'email', 'address', 'notes'}",
    )

    # Snapshot fields
    debtor_name = serializers.CharField(
        required=False, max_length=255, help_text="Debtor's full name (snapshot)"
    )
    debtor_contact = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
        help_text="Debtor's contact (snapshot)",
    )
    debtor_email = serializers.EmailField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Debtor's email (snapshot)",
    )
    debtor_address = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Debtor's address (snapshot)",
    )

    requested_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True, help_text="Amount requested"
    )
    purpose = serializers.CharField(
        required=True, max_length=255, help_text="Purpose of the loan"
    )
    proposed_due_date = serializers.DateField(
        required=True, help_text="Proposed due date"
    )
    interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Proposed interest rate",
    )

    class Meta:
        model = LoanApplication
        fields = [
            "debtor_id",
            "new_debtor",
            "debtor_name",
            "debtor_contact",
            "debtor_email",
            "debtor_address",
            "requested_amount",
            "purpose",
            "proposed_due_date",
            "interest_rate",
        ]

    def validate_new_debtor(self, value):
        """Validate new debtor data."""
        if value:
            required_fields = ["name"]
            for field in required_fields:
                if not value.get(field):
                    raise serializers.ValidationError(
                        {field: f"{field} is required when creating a new debtor."}
                    )
        return value

    def validate(self, data):
        """
        Cross-field validation.
        """
        debtor_id = data.get("debtor")
        new_debtor = data.get("new_debtor")
        debtor_name = data.get("debtor_name")

        # Either debtor_id or new_debtor must be provided
        if not debtor_id and not new_debtor:
            raise serializers.ValidationError(
                {
                    "debtor_id": "Either existing debtor_id or new_debtor data must be provided."
                }
            )

        # If new_debtor is provided, validate required fields
        if new_debtor:
            # Set debtor_name from new_debtor
            if not debtor_name:
                data["debtor_name"] = new_debtor.get("name")
            data["debtor_contact"] = new_debtor.get("contact")
            data["debtor_email"] = new_debtor.get("email")
            data["debtor_address"] = new_debtor.get("address")

        # If debtor_id is provided, use existing debtor data for snapshot
        if debtor_id and not debtor_name:
            data["debtor_name"] = debtor_id.name
            data["debtor_contact"] = debtor_id.contact
            data["debtor_email"] = debtor_id.email
            data["debtor_address"] = debtor_id.address

        # Ensure debtor_name is set
        if not data.get("debtor_name"):
            raise serializers.ValidationError(
                {"debtor_name": "Debtor name is required."}
            )

        # Validate requested amount
        if data.get("requested_amount", 0) <= 0:
            raise serializers.ValidationError(
                {"requested_amount": "Requested amount must be greater than 0."}
            )

        return data

    def create(self, validated_data):
        """Create a new loan application."""
        # Remove new_debtor as it's not a model field
        validated_data.pop("new_debtor", None)

        # Status is automatically set to 'pending' in the model
        return LoanApplication.objects.create(**validated_data)


class LoanApplicationUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing loan application.
    Only allowed for pending applications.
    """

    debtor = serializers.PrimaryKeyRelatedField(
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=False,
        help_text="ID of the debtor",
    )
    requested_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, help_text="Amount requested"
    )
    purpose = serializers.CharField(
        required=False, max_length=255, help_text="Purpose of the loan"
    )
    proposed_due_date = serializers.DateField(
        required=False, help_text="Proposed due date"
    )
    interest_rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Proposed interest rate",
    )

    class Meta:
        model = LoanApplication
        fields = [
            "debtor",
            "debtor_name",
            "debtor_contact",
            "debtor_email",
            "debtor_address",
            "requested_amount",
            "purpose",
            "proposed_due_date",
            "interest_rate",
        ]
        extra_kwargs = {
            "debtor": {"required": False, "allow_null": True},
            "debtor_name": {"required": False},
            "debtor_contact": {
                "required": False,
                "allow_blank": True,
                "allow_null": True,
            },
            "debtor_email": {
                "required": False,
                "allow_blank": True,
                "allow_null": True,
            },
            "debtor_address": {
                "required": False,
                "allow_blank": True,
                "allow_null": True,
            },
            "requested_amount": {"required": False},
            "purpose": {"required": False},
            "proposed_due_date": {"required": False},
            "interest_rate": {"required": False, "allow_null": True},
        }

    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance

        # Cannot update approved or rejected applications
        if instance and instance.status != LoanApplication.Status.PENDING:
            raise serializers.ValidationError(
                f"Cannot update application with status {instance.status}."
            )

        # If debtor is changed, update snapshot fields
        if data.get("debtor"):
            debtor = data["debtor"]
            if not data.get("debtor_name"):
                data["debtor_name"] = debtor.name
            if not data.get("debtor_contact"):
                data["debtor_contact"] = debtor.contact
            if not data.get("debtor_email"):
                data["debtor_email"] = debtor.email
            if not data.get("debtor_address"):
                data["debtor_address"] = debtor.address

        # Validate requested amount
        if data.get("requested_amount", 0) <= 0:
            raise serializers.ValidationError(
                {"requested_amount": "Requested amount must be greater than 0."}
            )

        return data

    def update(self, instance, validated_data):
        """Update an existing loan application."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class LoanApplicationApproveSerializer(serializers.Serializer):
    """
    Serializer for approving a loan application.
    """

    approved_by = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Name or ID of the person approving the application",
    )
    approved_at = serializers.DateTimeField(
        required=False, help_text="When the application was approved (defaults to now)"
    )

    def validate(self, data):
        """Validate that the application is in pending status."""
        instance = self.instance

        if not instance:
            raise serializers.ValidationError({"detail": "Application not found."})

        if instance.status != LoanApplication.Status.PENDING:
            raise serializers.ValidationError(
                {"detail": f"Cannot approve application with status {instance.status}."}
            )

        return data

    def save(self, **kwargs):
        instance = self.instance

        # The application must have a debtor at this point
        if not instance.debtor:
            raise serializers.ValidationError(
                {
                    "detail": "Cannot approve: no debtor associated with this application."
                }
            )

        instance.status = LoanApplication.Status.APPROVED
        instance.approved_at = self.validated_data.get("approved_at", timezone.now())
        instance.approved_by = self.validated_data["approved_by"]
        instance.save()

        return instance


class LoanApplicationRejectSerializer(serializers.Serializer):
    """
    Serializer for rejecting a loan application.
    """

    rejection_reason = serializers.CharField(
        required=True, help_text="Reason for rejection"
    )

    def validate(self, data):
        """Validate that the application is in pending status."""
        instance = self.instance

        if not instance:
            raise serializers.ValidationError({"detail": "Application not found."})

        if instance.status != LoanApplication.Status.PENDING:
            raise serializers.ValidationError(
                {"detail": f"Cannot reject application with status {instance.status}."}
            )

        return data

    def save(self, **kwargs):
        """Reject the application."""
        instance = self.instance
        instance.status = LoanApplication.Status.REJECTED
        instance.rejected_at = timezone.now()
        instance.rejection_reason = self.validated_data["rejection_reason"]
        instance.save()
        return instance
