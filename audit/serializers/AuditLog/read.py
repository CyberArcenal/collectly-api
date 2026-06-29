# audit/serializers/AuditLog/read.py
from rest_framework import serializers
from audit.models.log import AuditLog


class AuditLogReadSerializer(serializers.ModelSerializer):
    """Read-only serializer for audit log detail view."""
    user_display = serializers.SerializerMethodField(read_only=True)
    summary = serializers.SerializerMethodField(read_only=True)
    action_type_display = serializers.CharField(source='get_action_type_display', read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "event_id",
            "user",
            "user_display",
            "action_type",
            "action_type_display",
            "model_name",
            "object_id",
            "changes",
            "ip_address",
            "user_agent",
            "is_suspicious",
            "suspicious_reason",
            "timestamp",
            "summary",
        ]
        read_only_fields = ["__all__"]

    def get_user_display(self, obj):
        if obj.user:
            return getattr(obj.user, "username", str(obj.user))
        return None

    def get_summary(self, obj):
        return (
            f"[{obj.action_type}] {obj.model_name} ({obj.object_id}) "
            f"by {self.get_user_display(obj) or 'System'}"
        )


class AuditLogListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for listing audit logs."""
    user_display = serializers.SerializerMethodField(read_only=True)
    action_type_display = serializers.CharField(source='get_action_type_display', read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "event_id",
            "user_display",
            "action_type",
            "action_type_display",
            "model_name",
            "object_id",
            "is_suspicious",
            "timestamp",
        ]
        read_only_fields = ["__all__"]

    def get_user_display(self, obj):
        if obj.user:
            return getattr(obj.user, "username", str(obj.user))
        return None