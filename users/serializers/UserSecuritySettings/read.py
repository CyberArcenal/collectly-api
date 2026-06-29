# users/serializers/UserSecuritySettings/read.py
from rest_framework import serializers

from users.models.user_security_settings import UserSecuritySettings


class UserSecuritySettingsReadSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = UserSecuritySettings
        fields = [
            "id",
            "user",
            "user_username",
            "user_email",
            "two_factor_enabled",
            "recovery_email",
            "recovery_phone",
            "alert_on_new_device",
            "alert_on_password_change",
            "alert_on_failed_login",
            "updated_at",
            "created_at",
        ]
        read_only_fields = ["__all__"]