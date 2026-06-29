# users/serializers/SecurityLog/main.py
from rest_framework import serializers

from users.models.security_log import SecurityLog


class SecurityLogReadSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = SecurityLog
        fields = [
            "id",
            "user",
            "user_username",
            "event_type",
            "ip_address",
            "user_agent",
            "details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "user_username", "created_at", "updated_at"]