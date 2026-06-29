# users/serializers/validators.py
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError


class PasswordValidator:
    """Validator for password strength."""
    
    @staticmethod
    def validate(value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value


class UniqueValidator:
    """Validator for unique fields."""
    
    def __init__(self, model, field):
        self.model = model
        self.field = field
    
    def __call__(self, value):
        if self.model.objects.filter(**{self.field: value}).exists():
            raise serializers.ValidationError(
                f"{self.field.capitalize()} already exists."
            )
        return value