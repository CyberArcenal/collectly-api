from rest_framework import serializers
from django.utils import timezone

from notifications.models.notification_log import NotificationLog


# ---------- Minimal (used as nested relation) ----------
class NotificationLogMinimalSerializer(serializers.ModelSerializer):
    """Ultra‑lightweight serializer for notification log references."""
    class Meta:
        model = NotificationLog
        fields = ['id', 'recipient_email', 'status', 'sent_at']
        read_only_fields = ['__all__']


# ---------- List (lightweight) ----------
class NotificationLogListSerializer(serializers.ModelSerializer):
    """Lightweight read-only serializer for list views."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    # CamelCase aliases
    recipientEmail = serializers.EmailField(source='recipient_email', read_only=True)
    retryCount = serializers.IntegerField(source='retry_count', read_only=True)
    sentAt = serializers.DateTimeField(source='sent_at', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    statusDisplay = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = NotificationLog
        fields = [
            'id',
            'recipient_email',
            'subject',
            'status',
            'status_display',
            'retry_count',
            'sent_at',
            'created_at',
            # CamelCase aliases
            'recipientEmail',
            'retryCount',
            'sentAt',
            'createdAt',
            'statusDisplay',
        ]
        read_only_fields = ['__all__']


# ---------- Read (full detail) ----------
class NotificationLogReadSerializer(serializers.ModelSerializer):
    """Full read-only serializer for detail view."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    # CamelCase aliases
    recipientEmail = serializers.EmailField(source='recipient_email', read_only=True)
    errorMessage = serializers.CharField(source='error_message', read_only=True)
    retryCount = serializers.IntegerField(source='retry_count', read_only=True)
    resendCount = serializers.IntegerField(source='resend_count', read_only=True)
    sentAt = serializers.DateTimeField(source='sent_at', read_only=True)
    lastErrorAt = serializers.DateTimeField(source='last_error_at', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    statusDisplay = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = NotificationLog
        fields = [
            'id',
            'recipient_email',
            'subject',
            'payload',
            'status',
            'status_display',
            'error_message',
            'retry_count',
            'resend_count',
            'sent_at',
            'last_error_at',
            'created_at',
            'updated_at',
            # CamelCase aliases
            'recipientEmail',
            'errorMessage',
            'retryCount',
            'resendCount',
            'sentAt',
            'lastErrorAt',
            'createdAt',
            'updatedAt',
            'statusDisplay',
        ]
        read_only_fields = ['__all__']


# ---------- Create / Update / Retry (completely unchanged) ----------
class NotificationLogCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a new notification log.
    """
    
    recipient_email = serializers.EmailField(
        required=True,
        help_text="Recipient email address"
    )
    subject = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        help_text="Email subject"
    )
    payload = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Message content (HTML or text)"
    )
    status = serializers.ChoiceField(
        choices=NotificationLog.Status.choices,
        required=False,
        default=NotificationLog.Status.QUEUED,
        help_text="Delivery status"
    )
    
    class Meta:
        model = NotificationLog
        fields = [
            'recipient_email',
            'subject',
            'payload',
            'status',
        ]
    
    def create(self, validated_data):
        """Create a new notification log."""
        return NotificationLog.objects.create(**validated_data)


class NotificationLogUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for updating an existing notification log.
    """
    
    status = serializers.ChoiceField(
        choices=NotificationLog.Status.choices,
        required=False,
        help_text="Delivery status"
    )
    error_message = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="Error message if delivery failed"
    )
    
    class Meta:
        model = NotificationLog
        fields = [
            'status',
            'error_message',
        ]
        extra_kwargs = {
            'status': {'required': False},
            'error_message': {'required': False, 'allow_blank': True, 'allow_null': True},
        }
    
    def validate(self, data):
        """
        Cross-field validation.
        """
        instance = self.instance
        status = data.get('status')
        
        # If status is SENT, clear error_message
        if status == NotificationLog.Status.SENT:
            data['error_message'] = None
            data['sent_at'] = timezone.now()
        
        # If status is FAILED, require error_message
        if status == NotificationLog.Status.FAILED:
            if not data.get('error_message') and not instance.error_message:
                raise serializers.ValidationError({
                    'error_message': 'Error message is required when marking as failed.'
                })
            if not data.get('error_message'):
                data['error_message'] = instance.error_message
        
        return data
    
    def update(self, instance, validated_data):
        """Update an existing notification log."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class NotificationLogRetrySerializer(serializers.Serializer):
    """
    Serializer for retrying a failed notification.
    """
    
    confirm = serializers.BooleanField(
        required=True,
        help_text="Confirm to retry the notification"
    )
    
    def validate_confirm(self, value):
        """Validate confirmation."""
        if not value:
            raise serializers.ValidationError("Please confirm to retry this notification.")
        return value
    
    def validate(self, data):
        """
        Validate that the notification is in a retryable status.
        """
        instance = self.instance
        
        if not instance:
            raise serializers.ValidationError({
                'detail': 'Notification log not found.'
            })
        
        if instance.status not in [NotificationLog.Status.FAILED, NotificationLog.Status.QUEUED]:
            raise serializers.ValidationError({
                'detail': f'Cannot retry notification with status {instance.status}.'
            })
        
        return data