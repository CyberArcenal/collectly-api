# users/serializers/write.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from users.serializers.User.base import BaseSerializer
from users.serializers.User.fields import Base64ImageField
from users.serializers.User.validators import PasswordValidator, UniqueValidator



User = get_user_model()


class UserWriteSerializer(BaseSerializer):
    """
    Write serializer for user creation and updates.
    """
    password = serializers.CharField(
        write_only=True,
        required=False,
        validators=[PasswordValidator.validate]
    )
    password_confirmation = serializers.CharField(
        write_only=True,
        required=False
    )
    avatar = Base64ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "password",
            "password_confirmation",
            "phone_number",
            "user_type",
            "status",
            "avatar",
        ]
        extra_kwargs = {
            "username": {
                "validators": [
                    UniqueValidator(User, "username")
                ]
            },
            "email": {
                "validators": [
                    UniqueValidator(User, "email")
                ]
            },
        }

    def validate(self, data):
        """
        Validate password confirmation and other cross-field validations.
        """
        password = data.get("password")
        password_confirmation = data.get("password_confirmation")

        if password and password != password_confirmation:
            raise serializers.ValidationError({
                "password_confirmation": "Passwords do not match."
            })

        # Check if password is required for creation
        if self.instance is None and not password:
            raise serializers.ValidationError({
                "password": "Password is required when creating a user."
            })

        return data

    def create(self, validated_data):
        """Create a new user."""
        validated_data.pop("password_confirmation", None)
        password = validated_data.pop("password", None)

        user = User.objects.create(**validated_data)

        if password:
            user.set_password(password)
            user.save(update_fields=["password"])

        return user

    def update(self, instance, validated_data):
        """Update an existing user."""
        validated_data.pop("password_confirmation", None)
        password = validated_data.pop("password", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()
        return instance


class UserCreateSerializer(UserWriteSerializer):
    """
    Serializer for user creation with required fields.
    """
    class Meta(UserWriteSerializer.Meta):
        fields = UserWriteSerializer.Meta.fields
        extra_kwargs = {
            **UserWriteSerializer.Meta.extra_kwargs,
            "username": {
                **UserWriteSerializer.Meta.extra_kwargs.get("username", {}),
                "required": True,
            },
            "email": {
                **UserWriteSerializer.Meta.extra_kwargs.get("email", {}),
                "required": True,
            },
        }

    def validate(self, data):
        data = super().validate(data)
        
        # Ensure required fields for creation
        required_fields = ["username", "email", "password"]
        for field in required_fields:
            if not data.get(field):
                raise serializers.ValidationError({
                    field: f"{field.replace('_', ' ').title()} is required."
                })
        
        return data


class UserUpdateSerializer(UserWriteSerializer):
    """
    Serializer for user updates (password excluded).
    """
    class Meta(UserWriteSerializer.Meta):
        fields = [field for field in UserWriteSerializer.Meta.fields 
                  if field not in ["password", "password_confirmation"]]
        extra_kwargs = {
            **UserWriteSerializer.Meta.extra_kwargs,
            "username": {
                **UserWriteSerializer.Meta.extra_kwargs.get("username", {}),
                "required": False,
                # Alisin ang mga validators mula sa parent para hindi sila mag‑interfere
                "validators": [],
            },
            "email": {
                **UserWriteSerializer.Meta.extra_kwargs.get("email", {}),
                "required": False,
                "validators": [],
            },
        }

    def validate_username(self, value):
        # Kung hindi nagbago ang username, skip validation
        if self.instance and self.instance.username == value:
            return value
        # Check kung may ibang user na gumagamit nito
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_email(self, value):
        if self.instance and self.instance.email == value:
            return value
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value

    def validate(self, data):
        # Remove password fields (hindi dapat i‑update dito)
        data.pop("password", None)
        data.pop("password_confirmation", None)
        return data


class ChangePasswordSerializer(serializers.Serializer):
    """
    Serializer for password change.
    """
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(
        required=True,
        validators=[PasswordValidator.validate]
    )
    new_password_confirmation = serializers.CharField(required=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate(self, data):
        if data["new_password"] != data["new_password_confirmation"]:
            raise serializers.ValidationError({
                "new_password_confirmation": "New passwords do not match."
            })
        return data

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        return user