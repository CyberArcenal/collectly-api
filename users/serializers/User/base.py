# users/serializers/base.py
from rest_framework import serializers


class BaseSerializer(serializers.ModelSerializer):
    """
    Base serializer with common functionality for all user serializers.
    """
    class Meta:
        abstract = True

    def validate(self, data):
        """
        Common validation logic that applies to all user serializers.
        Override in child classes as needed.
        """
        return data


class TimestampMixin(serializers.Serializer):
    """Mixin for timestamp fields."""
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class StatusMixin(serializers.Serializer):
    """Mixin for status fields."""
    status_display = serializers.SerializerMethodField(read_only=True)
    is_restricted = serializers.BooleanField(read_only=True)
    is_suspended = serializers.BooleanField(read_only=True)


class UserTypeMixin(serializers.Serializer):
    user_type_display = serializers.SerializerMethodField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    is_manager = serializers.BooleanField(read_only=True)

    def get_user_type_display(self, obj):
        return obj.get_user_type_display()
    
    def get_status_display(self, obj):
        return obj.get_status_display()