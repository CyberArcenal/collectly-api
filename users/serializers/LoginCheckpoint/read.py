# users/serializers/LoginCheckpoint/read.py
from rest_framework import serializers
from django.utils import timezone

from users.models.login_checkpoint import LoginCheckpoint
from users.serializers.User import UserNestedSerializer


class LoginCheckpointReadSerializer(serializers.ModelSerializer):
    user_data = UserNestedSerializer(source='user', read_only=True)
    status_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LoginCheckpoint
        fields = [
            "id",
            "user_data",
            "token",
            "created_at",
            "expires_at",
            "is_used",
            "status_display",
        ]
        read_only_fields = ["__all__"]

    def get_status_display(self, obj):
        if obj.is_used:
            return "Used"
        elif timezone.now() > obj.expires_at:
            return "Expired"
        return "Active"