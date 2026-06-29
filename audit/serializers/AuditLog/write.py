# audit/serializers/AuditLog/write.py
from rest_framework import serializers
from audit.models.log import AuditLog
from users.models import User
from audit.services.log import AuditLogService


class AuditLogWriteSerializer(serializers.ModelSerializer):
    """Write serializer for audit log creation."""
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="User associated with the action"
    )
    action_type = serializers.ChoiceField(
        choices=AuditLog.ACTION_TYPES,
        required=True,
        help_text="Type of action performed"
    )
    model_name = serializers.CharField(
        required=True,
        max_length=100,
        help_text="Name of the affected model"
    )
    object_id = serializers.CharField(
        required=True,
        max_length=100,
        help_text="ID of the affected object"
    )
    changes = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Changes made (JSON format)"
    )
    ip_address = serializers.IPAddressField(
        required=False,
        allow_null=True,
        help_text="IP address of the requester"
    )
    user_agent = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=500,
        help_text="User agent of the requester"
    )
    is_suspicious = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether the action is flagged as suspicious"
    )
    suspicious_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        help_text="Reason for suspicious flag"
    )

    class Meta:
        model = AuditLog
        fields = [
            "user",
            "action_type",
            "model_name",
            "object_id",
            "changes",
            "ip_address",
            "user_agent",
            "is_suspicious",
            "suspicious_reason",
        ]

    def validate_action_type(self, value):
        valid_actions = [choice[0] for choice in AuditLog.ACTION_TYPES]
        if value not in valid_actions:
            raise serializers.ValidationError(
                f"Invalid action_type '{value}'. Must be one of {valid_actions}."
            )
        return value

    def validate(self, data):
        """Cross-field validation."""
        # If suspicious is True, suspicious_reason should be provided
        if data.get("is_suspicious") and not data.get("suspicious_reason"):
            raise serializers.ValidationError({
                "suspicious_reason": "Suspicious reason is required when is_suspicious is True."
            })

        return data

    def create(self, validated_data):
        """Use service to create audit log."""
        return AuditLogService.create_log(validated_data)

    # AuditLogs are immutable - prevent updates
    def update(self, instance, validated_data):
        raise serializers.ValidationError("AuditLog entries are immutable and cannot be updated.")