# users/serializers/OtpRequest/read.py
from rest_framework import serializers
from django.utils import timezone

from users.models.otp_request import OtpRequest
from users.serializers.User import UserNestedSerializer


class OtpRequestReadSerializer(serializers.ModelSerializer):
    """Read-only serializer for OTP requests."""
    user_data = UserNestedSerializer(source='user', read_only=True)
    status_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = OtpRequest
        fields = [
            "id",
            "user_data",
            "otp_code",
            "email",
            "created_at",
            "expires_at",
            "is_used",
            "attempt_count",
            "status_display",
        ]
        read_only_fields = ["__all__"]

    def get_status_display(self, obj):
        if obj.is_used:
            return "Used"
        elif timezone.now() > obj.expires_at:
            return "Expired"
        return "Active"