import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.models.notification import Notification
from notifications.services.notification import NotificationService
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


class NotificationStateTransitionService:
    """
    Service for handling notification state transitions.

    Handles notification creation, reading, and dismissal events.
    Manages WebSocket broadcasts for real-time UI updates.
    """

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    def _broadcast_to_websocket(channel, data):
        """
        Broadcast event to WebSocket clients.

        Args:
            channel: WebSocket channel name
            data: Data to broadcast
        """
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "notifications",
                {
                    "type": "send_notification",
                    "channel": channel,
                    "data": data,
                }
            )
        except Exception as e:
            logger.warning(f"[Notification] Failed to broadcast to WebSocket: {e}")

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_created(notification, user="system", request=None):
        """
        Handle post-notification creation events.

        Args:
            notification: Notification instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Notification: The created notification instance
        """
        logger.info(
            f"[NotificationTransition] on_created: "
            f"notification_id={notification.id}, title={notification.title}, "
            f"type={notification.type}, user={user}"
        )

        # Broadcast to UI for toast popup
        NotificationStateTransitionService._broadcast_to_websocket(
            "notification:created",
            {
                "id": notification.id,
                "title": notification.title,
                "message": notification.message,
                "type": notification.type,
                "created_at": notification.created_at.isoformat() if notification.created_at else None,
            }
        )

        # Audit log (optional - already logged by NotificationService)
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_create',
            model_name='Notification',
            object_id=str(notification.id),
            changes={
                'title': notification.title,
                'type': notification.type,
            }
        )

        logger.info(f"[NotificationTransition] Notification #{notification.id} created")
        return notification

    @staticmethod
    @transaction.atomic
    def on_read(notification, user="system", request=None):
        """
        Mark a notification as read.

        Args:
            notification: Notification instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Notification: The updated notification instance

        Raises:
            ValidationError: If notification not found
        """
        logger.info(
            f"[NotificationTransition] on_read: "
            f"notification_id={notification.id}, user={user}"
        )

        # Store old state for audit
        old_is_read = notification.is_read

        # Mark as read
        notification.is_read = True
        notification.updated_at = timezone.now()
        notification.save(update_fields=['is_read', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_read',
            model_name='Notification',
            object_id=str(notification.id),
            changes={
                'before': {'is_read': old_is_read},
                'after': {'is_read': True},
            }
        )

        logger.info(f"[NotificationTransition] Notification #{notification.id} marked as read")
        return notification

    @staticmethod
    @transaction.atomic
    def on_dismiss(notification, user="system", request=None):
        """
        Dismiss a notification without reading (e.g., swipe away).

        Args:
            notification: Notification instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Notification: The dismissed notification instance
        """
        logger.info(
            f"[NotificationTransition] on_dismiss: "
            f"notification_id={notification.id}, user={user}"
        )

        # Store old state for audit
        old_is_read = notification.is_read

        # Mark as read (dismissed)
        notification.is_read = True
        notification.updated_at = timezone.now()
        notification.save(update_fields=['is_read', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='notification_dismiss',
            model_name='Notification',
            object_id=str(notification.id),
            changes={
                'before': {'is_read': old_is_read},
                'after': {'is_read': True, 'dismissed': True},
            }
        )

        logger.info(f"[NotificationTransition] Notification #{notification.id} dismissed")
        return notification