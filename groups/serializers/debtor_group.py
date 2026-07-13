from rest_framework import serializers

from groups.models.debtor_group import DebtorGroup


# ---------- Minimal (used as nested relation) ----------
class DebtorGroupMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for debtor group references."""
    class Meta:
        model = DebtorGroup
        fields = ['id', 'name', 'color']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class DebtorGroupListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    member_count = serializers.SerializerMethodField()

    # CamelCase aliases
    memberCount = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = DebtorGroup
        fields = [
            'id',
            'name',
            'description',
            'color',
            'member_count',
            'created_at',
            'memberCount',
            'createdAt',
            'updatedAt',
        ]
        read_only_fields = ['__all__']

    def get_member_count(self, obj):
        return obj.member_count

    def get_memberCount(self, obj):
        return obj.member_count


# ---------- Read (full detail) ----------
class DebtorGroupReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""
    member_count = serializers.SerializerMethodField()
    total_debt = serializers.SerializerMethodField()
    active_members_count = serializers.SerializerMethodField()

    # CamelCase aliases
    memberCount = serializers.SerializerMethodField()
    totalDebt = serializers.SerializerMethodField()
    activeMembersCount = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)

    class Meta:
        model = DebtorGroup
        fields = [
            'id',
            'name',
            'description',
            'color',
            'member_count',
            'total_debt',
            'active_members_count',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            'memberCount',
            'totalDebt',
            'activeMembersCount',
            'createdAt',
            'updatedAt',
            'deletedAt',
        ]
        read_only_fields = ['__all__']

    def get_member_count(self, obj):
        return obj.member_count

    def get_total_debt(self, obj):
        return obj.total_debt

    def get_active_members_count(self, obj):
        return obj.active_members.count()

    def get_memberCount(self, obj):
        return obj.member_count

    def get_totalDebt(self, obj):
        return obj.total_debt

    def get_activeMembersCount(self, obj):
        return obj.active_members.count()


# ---------- Create / Update (completely unchanged) ----------


class DebtorGroupCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new debtor group.
    """
    
    name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Name of the group (must be unique)"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of the group"
    )
    color = serializers.CharField(
        required=False,
        max_length=7,
        default='#3b82f6',
        help_text="Hex color code (e.g., '#3b82f6')"
    )
    
    class Meta:
        model = DebtorGroup
        fields = [
            'name',
            'description',
            'color',
        ]
    
    def validate_name(self, value):
        """Validate name uniqueness."""
        if DebtorGroup.objects.filter(name=value).exists():
            raise serializers.ValidationError("Group name already exists.")
        return value
    
    def validate_color(self, value):
        """Validate hex color format."""
        if value and not value.startswith('#'):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., '#3b82f6').")
        if value and len(value) not in [4, 7]:
            raise serializers.ValidationError("Color must be a valid hex code (e.g., '#3b82f6').")
        return value


class DebtorGroupUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing debtor group.
    """
    
    name = serializers.CharField(
        required=False,
        max_length=255,
        help_text="Name of the group (must be unique)"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Description of the group"
    )
    color = serializers.CharField(
        required=False,
        max_length=7,
        help_text="Hex color code (e.g., '#3b82f6')"
    )
    
    class Meta:
        model = DebtorGroup
        fields = [
            'name',
            'description',
            'color',
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False, 'allow_blank': True, 'allow_null': True},
            'color': {'required': False},
        }
    
    def validate_name(self, value):
        """Validate name uniqueness (excluding current instance)."""
        if value:
            existing = DebtorGroup.objects.filter(name=value)
            if self.instance:
                existing = existing.exclude(id=self.instance.id)
            if existing.exists():
                raise serializers.ValidationError("Group name already exists.")
        return value
    
    def validate_color(self, value):
        """Validate hex color format."""
        if value and not value.startswith('#'):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., '#3b82f6').")
        if value and len(value) not in [4, 7]:
            raise serializers.ValidationError("Color must be a valid hex code (e.g., '#3b82f6').")
        return value
    
    def update(self, instance, validated_data):
        """Update an existing group."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance