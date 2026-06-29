# audit/serializers/AuditPolicy/read.py
from rest_framework import serializers
from audit.models.policy import AuditPolicy


class AuditPolicyReadSerializer(serializers.ModelSerializer):
    """Read-only serializer for audit policy detail view."""
    policy_summary = serializers.SerializerMethodField(read_only=True)
    immutable_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AuditPolicy
        fields = [
            "id",
            "retention_years",
            "immutable",
            "immutable_display",
            "created_at",
            "policy_summary",
        ]
        read_only_fields = ["__all__"]

    def get_policy_summary(self, obj):
        return f"Retention: {obj.retention_years} years | Immutable: {obj.immutable}"

    def get_immutable_display(self, obj):
        return "Yes" if obj.immutable else "No"


class AuditPolicyListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for listing audit policies."""
    policy_summary = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AuditPolicy
        fields = [
            "id",
            "retention_years",
            "immutable",
            "created_at",
            "policy_summary",
        ]
        read_only_fields = ["__all__"]

    def get_policy_summary(self, obj):
        return f"Retention: {obj.retention_years} years | Immutable: {obj.immutable}"