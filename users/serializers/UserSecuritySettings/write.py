# users/serializers/UserSecuritySettings/write.py
from rest_framework import serializers

from users.models.user_security_settings import UserSecuritySettings


class UserSecuritySettingsWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSecuritySettings
        fields = [
            "two_factor_enabled",
            "recovery_email",
            "recovery_phone",
            "alert_on_new_device",
            "alert_on_password_change",
            "alert_on_failed_login",
        ]

    def validate_recovery_email(self, value):
        if value:
            user = self.context.get('request').user if self.context.get('request') else None
            if user and value.lower() == user.email.lower():
                raise serializers.ValidationError(
                    "Recovery email must be different from primary email"
                )
        return value

    def validate_recovery_phone(self, value):
        if value:
            if not value.replace('+', '').replace(' ', '').isdigit():
                raise serializers.ValidationError(
                    "Phone number must contain only digits, spaces, and plus sign"
                )
            if len(value) < 10 or len(value) > 20:
                raise serializers.ValidationError(
                    "Phone number must be between 10 and 20 characters"
                )
        return value