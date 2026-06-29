# users/serializers/OtpRequest/main.py
from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model

from users.models.otp_request import OtpRequest
from users.serializers.User import UserNestedSerializer

User = get_user_model()


class OtpRequestSerializer(serializers.ModelSerializer):
    user_data = UserNestedSerializer(source='user', read_only=True)
    status_display = serializers.SerializerMethodField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    email = serializers.EmailField(required=False, allow_null=True)

    class Meta:
        model = OtpRequest
        fields = [
            "id",
            "user",
            "user_data",
            "otp_code",
            "email",
            "created_at",
            "expires_at",
            "is_used",
            "attempt_count",
            "status_display",
        ]
        read_only_fields = ["id", "created_at", "expires_at", "is_used", "attempt_count"]

    def get_status_display(self, obj):
        if obj.is_used:
            return "Used"
        elif timezone.now() > obj.expires_at:
            return "Expired"
        return "Active"

    def validate(self, data):
        user = data.get('user')
        email = data.get('email')

        if not user and not email:
            raise serializers.ValidationError("Either user or email must be provided.")

        if user and not email:
            data['email'] = user.email

        return data

    def create(self, validated_data):
        validated_data['expires_at'] = timezone.now() + timezone.timedelta(minutes=10)
        return OtpRequest.objects.create(**validated_data)