# users/serializers/nested.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from users.models.user_security_settings import UserSecuritySettings
from users.serializers.User.base import BaseSerializer


User = get_user_model()


class UserSecuritySettingsNestedSerializer(BaseSerializer):
    """Nested serializer for user security settings."""
    
    class Meta:
        model = UserSecuritySettings
        fields = [
            "id",
            "two_factor_enabled",
            "recovery_email",
            "recovery_phone",
            "alert_on_new_device",
            "alert_on_password_change",
            "alert_on_failed_login",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class UserNestedSerializer(BaseSerializer):
    """Minimal nested serializer for user references (e.g., in comments, orders)."""
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "full_name",
            "username",
            "email",
            "first_name",
            "last_name",
            "user_type",
            "avatar",
        ]

    def get_full_name(self, obj):
        return obj.get_full_name()


class UserMinimalSerializer(BaseSerializer):
    """Ultra-minimal serializer for user references (ID + name only)."""
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "full_name",
            "username",
            "avatar",
        ]

    def get_full_name(self, obj):
        return obj.get_full_name()