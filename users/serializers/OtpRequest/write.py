# users/serializers/OtpRequest/write.py
import random

from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model

from users.models.otp_request import OtpRequest

User = get_user_model()


class OtpRequestWriteSerializer(serializers.ModelSerializer):
    """Write serializer for OTP requests."""
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    email = serializers.EmailField(required=False, allow_null=True)

    class Meta:
        model = OtpRequest
        fields = [
            "user",
            "email",
            "type",
        ]

    def validate(self, data):
        user = data.get('user')
        email = data.get('email')

        if not user and not email:
            raise serializers.ValidationError(
                "Either user or email must be provided."
            )

        if user and not email:
            data['email'] = user.email

        return data

    def create(self, validated_data):
        validated_data['expires_at'] = timezone.now() + timezone.timedelta(minutes=10)
        validated_data['otp_code'] = f"{random.randint(0, 999999):06d}"
        return OtpRequest.objects.create(**validated_data)