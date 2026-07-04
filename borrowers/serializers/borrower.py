from rest_framework import serializers
from django.contrib.auth import get_user_model

from borrowers.models.borrower import Borrower
from users.serializers.User.nested import UserMinimalSerializer

User = get_user_model()


class BorrowerReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for borrower detail view.
    Includes computed properties and nested user data.
    """
    
    full_contact = serializers.SerializerMethodField()
    total_debt = serializers.SerializerMethodField()
    active_debt_count = serializers.SerializerMethodField()
    user_data = UserMinimalSerializer(source='user', read_only=True)

    # ✅ CamelCase fields for frontend compatibility
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = Borrower
        fields = [
            'id',
            'name',
            'contact',
            'email',
            'address',
            'notes',
            'user',
            'credit_rating',
            'user_data',
            'full_contact',
            'total_debt',
            'active_debt_count',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            # ✅ Added camelCase aliases
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_full_contact(self, obj):
        return obj.full_contact

    def get_total_debt(self, obj):
        return obj.total_debt

    def get_active_debt_count(self, obj):
        return obj.active_debt_count


class BorrowerListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for borrower list views.
    """
    
    full_contact = serializers.SerializerMethodField()

    # ✅ CamelCase fields for frontend compatibility
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = Borrower
        fields = [
            'id',
            'name',
            'contact',
            'email',
            'address',
            'full_contact',
            'credit_rating',
            'created_at',
            'updated_at',
            # ✅ Added camelCase aliases
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_full_contact(self, obj):
        return obj.full_contact


class BorrowerCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new borrower.
    """
    
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="Associated system user (optional)"
    )
    
    class Meta:
        model = Borrower
        fields = [
            'name',
            'contact',
            'email',
            'address',
            'notes',
            'user',
        ]
    
    def validate_email(self, value):
        """Validate email uniqueness."""
        if value and Borrower.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value
    
    def validate_contact(self, value):
        """Validate contact uniqueness."""
        if value and Borrower.objects.filter(contact=value).exists():
            raise serializers.ValidationError("Contact already exists.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        # Ensure at least name is provided
        if not data.get('name'):
            raise serializers.ValidationError({'name': 'Name is required.'})
        return data
    
    def create(self, validated_data):
        """Create a new borrower."""
        return Borrower.objects.create(**validated_data)


class BorrowerUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing borrower.
    """
    
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        help_text="Associated system user (optional)"
    )
    
    class Meta:
        model = Borrower
        fields = [
            'name',
            'contact',
            'email',
            'address',
            'notes',
            'user',
        ]
        extra_kwargs = {
            'name': {'required': False},
            'contact': {'required': False},
            'email': {'required': False},
            'address': {'required': False},
            'notes': {'required': False},
            'user': {'required': False},
        }
    
    def validate_email(self, value):
        """Validate email uniqueness (excluding current instance)."""
        if value:
            existing = Borrower.objects.filter(email=value)
            if self.instance:
                existing = existing.exclude(id=self.instance.id)
            if existing.exists():
                raise serializers.ValidationError("Email already exists.")
        return value
    
    def validate_contact(self, value):
        """Validate contact uniqueness (excluding current instance)."""
        if value:
            existing = Borrower.objects.filter(contact=value)
            if self.instance:
                existing = existing.exclude(id=self.instance.id)
            if existing.exists():
                raise serializers.ValidationError("Contact already exists.")
        return value
    
    def update(self, instance, validated_data):
        """Update an existing borrower."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance