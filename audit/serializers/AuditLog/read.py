# audit/serializers/AuditLog/read.py
from rest_framework import serializers
from audit.models.log import AuditLog


class AuditLogReadSerializer(serializers.ModelSerializer):
    """Read-only serializer for audit log detail view."""

    user_display = serializers.SerializerMethodField(read_only=True)
    summary = serializers.SerializerMethodField(read_only=True)
    action_type_display = serializers.CharField(
        source="get_action_type_display", read_only=True
    )
    action = serializers.SerializerMethodField()
    entity = serializers.CharField(source="model_name")
    entityId = serializers.CharField(source="object_id")
    user = serializers.SerializerMethodField()
    oldData = serializers.SerializerMethodField()
    newData = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "entity",
            "entityId",
            "user",
            "oldData",
            "newData",
            "event_id",
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

    def get_action(self, obj):
        return obj.action_type.upper()

    def get_user(self, obj):
        return obj.user.username if obj.user else None

    def get_oldData(self, obj):
        return obj.changes.get("old") if obj.changes else None

    def get_newData(self, obj):
        return obj.changes.get("new") if obj.changes else None


class AuditLogListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for listing audit logs."""

    user_display = serializers.SerializerMethodField(read_only=True)
    action_type_display = serializers.CharField(
        source="get_action_type_display", read_only=True
    )
    # ✅ Idinagdag ang mga field na kailangan ng frontend
    action = serializers.SerializerMethodField()
    entity = serializers.CharField(source="model_name")
    entityId = serializers.CharField(source="object_id")
    user = serializers.SerializerMethodField()

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
            # ✅ Bagong fields
            "action",
            "entity",
            "entityId",
            "user",
        ]
        read_only_fields = ["__all__"]

    def get_user_display(self, obj):
        if obj.user:
            return getattr(obj.user, "username", str(obj.user))
        return None

    def get_action(self, obj):
        return obj.action_type.upper()

    def get_user(self, obj):
        return obj.user.username if obj.user else None