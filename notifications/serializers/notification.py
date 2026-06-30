from rest_framework import serializers
from django.utils import timezone

from notifications.models.notification import Notification
from debts.models.debt import Debt
from debts.serializers.debt import DebtListSerializer


class NotificationReadSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for notification detail view.
    Includes computed properties and nested debt data.
    """
    
    debt_data = DebtListSerializer(source='debt', read_only=True)
    debt_name = serializers.CharField(source='debt.name', read_only=True, allow_null=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    is_read_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'debt',
            'debt_name',
            'debt_data',
            'title',
            'message',
            'type',
            'type_display',
            'is_read',
            'is_read_display',
            'scheduled_for',
            'is_read_display',
            'created_at',
            'updated_at',
            'deleted_at',
            'is_deleted',
        ]
        read_only_fields = ['__all__']
    
    def get_is_read_display(self, obj):
        return "Read" if obj.is_read else "Unread"


class NotificationListSerializer(serializers.ModelSerializer):
    """
    Lightweight read-only serializer for notification list views.
    """
    
    debt_name = serializers.CharField(source='debt.name', read_only=True, allow_null=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    is_read_display = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'debt',
            'debt_name',
            'title',
            'message',
            'type',
            'type_display',
            'is_read',
            'is_read_display',
            'scheduled_for',
            'created_at',
        ]
        read_only_fields = ['__all__']
    
    def get_is_read_display(self, obj):
        return "Read" if obj.is_read else "Unread"


class NotificationCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new notification.
    """
    
    debt_id = serializers.PrimaryKeyRelatedField(
        source='debt',
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="ID of the related debt (optional)"
    )
    title = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Notification title"
    )
    message = serializers.CharField(
        required=True,
        help_text="Notification message"
    )
    type = serializers.ChoiceField(
        choices=Notification.Type.choices,
        required=False,
        default=Notification.Type.REMINDER,
        help_text="Notification type"
    )
    is_read = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether the notification has been read"
    )
    scheduled_for = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="When the notification should be sent"
    )
    
    class Meta:
        model = Notification
        fields = [
            'debt_id',
            'title',
            'message',
            'type',
            'is_read',
            'scheduled_for',
        ]
    
    def validate_scheduled_for(self, value):
        """Validate scheduled_for is not in the past."""
        if value and value < timezone.now():
            raise serializers.ValidationError("Scheduled date cannot be in the past.")
        return value
    
    def validate(self, data):
        """Cross-field validation."""
        return data
    
    def create(self, validated_data):
        """Create a new notification."""
        return Notification.objects.create(**validated_data)


class NotificationUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing notification.
    """
    
    debt = serializers.PrimaryKeyRelatedField(
        queryset=Debt.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
        help_text="ID of the related debt"
    )
    title = serializers.CharField(
        required=False,
        max_length=255,
        help_text="Notification title"
    )
    message = serializers.CharField(
        required=False,
        help_text="Notification message"
    )
    type = serializers.ChoiceField(
        choices=Notification.Type.choices,
        required=False,
        help_text="Notification type"
    )
    is_read = serializers.BooleanField(
        required=False,
        help_text="Whether the notification has been read"
    )
    scheduled_for = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="When the notification should be sent"
    )
    
    class Meta:
        model = Notification
        fields = [
            'debt',
            'title',
            'message',
            'type',
            'is_read',
            'scheduled_for',
        ]
        extra_kwargs = {
            'debt': {'required': False, 'allow_null': True},
            'title': {'required': False},
            'message': {'required': False},
            'type': {'required': False},
            'is_read': {'required': False},
            'scheduled_for': {'required': False, 'allow_null': True},
        }
    
    def validate_scheduled_for(self, value):
        """Validate scheduled_for is not in the past."""
        if value and value < timezone.now():
            raise serializers.ValidationError("Scheduled date cannot be in the past.")
        return value
    
    def update(self, instance, validated_data):
        """Update an existing notification."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class NotificationMarkReadSerializer(serializers.Serializer):
    """
    Serializer for marking a notification as read/unread.
    """
    
    is_read = serializers.BooleanField(
        required=True,
        help_text="Set to true to mark as read, false to mark as unread"
    )
    
    def validate(self, data):
        """Validate that the notification exists."""
        instance = self.instance
        
        if not instance:
            raise serializers.ValidationError({
                'detail': 'Notification not found.'
            })
        
        return data
    
    def save(self, **kwargs):
        """Mark the notification as read/unread."""
        instance = self.instance
        instance.is_read = self.validated_data['is_read']
        instance.save(update_fields=['is_read', 'updated_at'])
        return instance


class NotificationMarkAllReadSerializer(serializers.Serializer):
    """
    Serializer for marking all notifications as read.
    """
    
    confirm = serializers.BooleanField(
        required=True,
        help_text="Confirm to mark all notifications as read"
    )
    
    def validate_confirm(self, value):
        """Validate confirmation."""
        if not value:
            raise serializers.ValidationError("Please confirm to mark all notifications as read.")
        return value