# users/serializers/LoginSession/read.py
from rest_framework import serializers
from django.utils import timezone

from users.models.login_session import LoginSession
from users.serializers.User import UserNestedSerializer


class LoginSessionReadSerializer(serializers.ModelSerializer):
    user_data = UserNestedSerializer(source='user', read_only=True)
    status_display = serializers.SerializerMethodField(read_only=True)
    is_valid_display = serializers.BooleanField(source='is_valid', read_only=True)

    class Meta:
        model = LoginSession
        fields = [
            "id",
            "user_data",
            "device_name",
            "ip_address",
            "created_at",
            "last_used",
            "expires_at",
            "is_active",
            "status_display",
            "is_valid_display",
        ]
        read_only_fields = ["__all__"]

    def get_status_display(self, obj):
        if not obj.is_active:
            return "Inactive"
        elif timezone.now() > obj.expires_at:
            return "Expired"
        return "Active"