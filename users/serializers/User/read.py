# users/serializers/read.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from users.serializers.User.base import BaseSerializer, StatusMixin, TimestampMixin, UserTypeMixin
from users.serializers.User.nested import UserSecuritySettingsNestedSerializer


User = get_user_model()


class UserReadSerializer(BaseSerializer, TimestampMixin, StatusMixin, UserTypeMixin):
    """
    Read-only serializer for user detail views (GET /users/<id>/).
    """
    full_name = serializers.SerializerMethodField()
    security_settings = UserSecuritySettingsNestedSerializer(read_only=True
    )

    class Meta:
        model = User
        fields = [
            # Basic info
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "avatar",
            
            # User type and status
            "user_type",
            "user_type_display",
            "status",
            "status_display",
            
            # Contact info
            "phone_number",
            
            # Timestamps
            "created_at",
            "updated_at",
            
            # Nested data
            "security_settings",
            
            # Status properties
            "is_restricted",
            "is_suspended",
            
            # Role properties
            "is_admin",
            "is_manager",
        ]
        read_only_fields = ["__all__"]

    def get_full_name(self, obj):
        return obj.get_full_name()


class UserListSerializer(BaseSerializer, TimestampMixin):
    """
    Read-only serializer for user list views (GET /users/).
    Lightweight version for better performance.
    """
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "avatar",
            "user_type",
            "status",
            "phone_number",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["__all__"]

    def get_full_name(self, obj):
        return obj.get_full_name()