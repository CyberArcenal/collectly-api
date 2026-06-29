# users/serializers/LoginSession/write.py
import uuid
from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model

from users.models.login_session import LoginSession

User = get_user_model()


class LoginSessionWriteSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True
    )

    class Meta:
        model = LoginSession
        fields = [
            "user",
            "device_name",
            "ip_address",
            "expires_at",
            "is_active",
        ]

    def validate(self, data):
        expires_at = data.get('expires_at')
        if expires_at and expires_at <= timezone.now():
            raise serializers.ValidationError({
                "expires_at": "Expiration date must be in the future."
            })
        return data

    def create(self, validated_data):
        validated_data['refresh_token'] = uuid.uuid4().hex
        validated_data['access_token'] = uuid.uuid4().hex
        return LoginSession.objects.create(**validated_data)