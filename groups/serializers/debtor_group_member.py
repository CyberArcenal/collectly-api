from rest_framework import serializers

from groups.models.debtor_group_member import DebtorGroupMember
from groups.models.debtor_group import DebtorGroup
from borrowers.models.borrower import Borrower
from borrowers.serializers.borrower import BorrowerMinimalSerializer
from groups.serializers.debtor_group import DebtorGroupMinimalSerializer


# ---------- Minimal (used as nested relation) ----------
class DebtorGroupMemberMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for group member references."""
    class Meta:
        model = DebtorGroupMember
        fields = ['id', 'assigned_at']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class DebtorGroupMemberListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    # ✅ Overwrite relations with minimal serializers
    group = DebtorGroupMinimalSerializer(read_only=True)
    debtor = BorrowerMinimalSerializer(read_only=True)

    # CamelCase aliases for fields not covered by nested objects
    assignedAt = serializers.DateTimeField(source='assigned_at', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = DebtorGroupMember
        fields = [
            'id',
            'group',          # nested minimal
            'debtor',         # nested minimal
            'assigned_at',
            'created_at',
            'assignedAt',
            'createdAt',
        ]
        read_only_fields = ['__all__']


# ---------- Read (full detail) ----------
class DebtorGroupMemberReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer with nested relations."""
    # ✅ Overwrite relations with minimal serializers
    group = DebtorGroupMinimalSerializer(read_only=True)
    debtor = BorrowerMinimalSerializer(read_only=True)

    is_active = serializers.SerializerMethodField()

    # CamelCase aliases for fields not covered by nested objects
    assignedAt = serializers.DateTimeField(source='assigned_at', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    deletedAt = serializers.DateTimeField(source='deleted_at', read_only=True)
    isActive = serializers.SerializerMethodField()

    class Meta:
        model = DebtorGroupMember
        fields = [
            'id',
            'group',
            'debtor',
            'assigned_at',
            'is_active',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
            'assignedAt',
            'createdAt',
            'updatedAt',
            'deletedAt',
            'isActive',
        ]
        read_only_fields = ['__all__']

    def get_is_active(self, obj):
        return obj.is_active

    def get_isActive(self, obj):
        return obj.is_active


# ---------- Create / Delete (completely unchanged) ----------



class DebtorGroupMemberCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for adding a debtor to a group.
    """
    
    group_id = serializers.PrimaryKeyRelatedField(
        source='group',
        queryset=DebtorGroup.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the group"
    )
    debtor_id = serializers.PrimaryKeyRelatedField(
        source='debtor',
        queryset=Borrower.objects.filter(deleted_at__isnull=True),
        required=True,
        help_text="ID of the debtor to add"
    )
    
    class Meta:
        model = DebtorGroupMember
        fields = [
            'group_id',
            'debtor_id',
        ]
    
    def validate(self, data):
        """Validate that the debtor is not already a member."""
        group = data.get('group')
        debtor = data.get('debtor')
        
        if group and debtor:
            # Check if membership already exists (including soft-deleted)
            existing = DebtorGroupMember.objects.filter(
                group=group,
                debtor=debtor
            ).first()
            
            if existing:
                if existing.deleted_at:
                    # Soft-deleted membership can be restored
                    # We'll handle this in the service layer
                    pass
                else:
                    raise serializers.ValidationError({
                        'debtor_id': f"Debtor is already a member of group '{group.name}'."
                    })
        
        return data
    
    def create(self, validated_data):
        """Create a new group member."""
        return DebtorGroupMember.objects.create(**validated_data)


class DebtorGroupMemberDeleteSerializer(serializers.Serializer):
    """
    Serializer for removing a debtor from a group.
    This is a write serializer for the delete operation (no model fields).
    """
    
    group_id = serializers.IntegerField(
        required=True,
        help_text="ID of the group"
    )
    debtor_id = serializers.IntegerField(
        required=True,
        help_text="ID of the debtor to remove"
    )
    
    def validate(self, data):
        """Validate that the member exists."""
        group_id = data.get('group_id')
        debtor_id = data.get('debtor_id')
        
        member = DebtorGroupMember.objects.filter(
            group_id=group_id,
            debtor_id=debtor_id,
            deleted_at__isnull=True
        ).first()
        
        if not member:
            raise serializers.ValidationError({
                'detail': 'Member not found in this group.'
            })
        
        # Store the member for use in the view/service
        data['member'] = member
        return data