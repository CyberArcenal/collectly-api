# audit/serializers/AuditPolicy/write.py
from rest_framework import serializers
from audit.models.policy import AuditPolicy
from audit.services.policy import AuditPolicyService


class AuditPolicyWriteSerializer(serializers.ModelSerializer):
    """Write serializer for audit policy operations."""
    retention_years = serializers.IntegerField(
        required=True,
        min_value=1,
        max_value=50,
        help_text="Number of years to retain audit logs"
    )
    immutable = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Whether the policy is immutable (cannot be changed after creation)"
    )

    class Meta:
        model = AuditPolicy
        fields = [
            "retention_years",
            "immutable",
        ]

    def validate_retention_years(self, value):
        if value <= 0:
            raise serializers.ValidationError("Retention years must be positive.")
        if value > 50:
            raise serializers.ValidationError(
                "Retention years cannot exceed 50 (compliance guardrail)."
            )
        return value

    def validate(self, data):
        """Cross-field validation."""
        # If updating an existing policy and it is immutable, raise error
        if self.instance and self.instance.immutable:
            raise serializers.ValidationError(
                "This policy is immutable and cannot be updated."
            )

        # If immutable is being changed from True to False, allow only if not locked
        if self.instance and self.instance.immutable and not data.get("immutable", True):
            raise serializers.ValidationError(
                "Cannot change an immutable policy to mutable."
            )

        return data

    def create(self, validated_data):
        """Use service to create policy."""
        return AuditPolicyService.create_policy(validated_data)

    def update(self, instance, validated_data):
        """Use service to update policy."""
        return AuditPolicyService.update_policy(instance, validated_data)