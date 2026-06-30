import logging
from django.db import transaction
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


class UserStateTransitionService:
    """
    Service for handling user status transitions.
    
    Triggered when a user's status changes (active, restricted, suspended, deleted).
    Manages audit logging, in-app notifications, and any side effects.
    """

    @staticmethod
    @transaction.atomic
    def on_status_change(user, old_status, new_status, actor=None, request=None):
        """
        Central dispatcher for user status changes.
        """
        logger.info(f"[UserTransition] User #{user.id} status: {old_status} → {new_status} by {actor or 'system'}")

        # Map status to specific handler
        handlers = {
            'active': UserStateTransitionService._on_activate,
            'restricted': UserStateTransitionService._on_restrict,
            'suspended': UserStateTransitionService._on_suspend,
            'deleted': UserStateTransitionService._on_delete,
        }

        handler = handlers.get(new_status)
        if handler:
            handler(user, old_status, actor, request)
        else:
            logger.warning(f"[UserTransition] No handler for status: {new_status}")

    # ------------------------------------------------------------------
    # Status-specific handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _on_activate(user, old_status, actor=None, request=None):
        """Handle activation: log, notify, etc."""
        log_audit_event(
            request=request,
            user=actor or user,
            action_type='status_change',
            model_name='User',
            object_id=str(user.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'active'},
            }
        )

        # In-app notification (to admin or user)
        NotificationService.create(
            data={
                'title': 'User Activated',
                'message': f'User "{user.username}" has been activated.',
                'type': 'info',
                'metadata': {'user_id': user.id, 'old_status': old_status},
            },
            user=actor or 'system',
            request=request
        )

        # Optionally send email to user
        # if email_enabled() and user.email: ...

        logger.info(f"[UserTransition] User #{user.id} activated")

    @staticmethod
    def _on_restrict(user, old_status, actor=None, request=None):
        """Handle restriction."""
        log_audit_event(
            request=request,
            user=actor or user,
            action_type='status_change',
            model_name='User',
            object_id=str(user.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'restricted'},
            }
        )

        NotificationService.create(
            data={
                'title': 'User Restricted',
                'message': f'User "{user.username}" has been restricted.',
                'type': 'warning',
                'metadata': {'user_id': user.id, 'old_status': old_status},
            },
            user=actor or 'system',
            request=request
        )

        logger.info(f"[UserTransition] User #{user.id} restricted")

    @staticmethod
    def _on_suspend(user, old_status, actor=None, request=None):
        """Handle suspension."""
        log_audit_event(
            request=request,
            user=actor or user,
            action_type='status_change',
            model_name='User',
            object_id=str(user.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'suspended'},
            }
        )

        NotificationService.create(
            data={
                'title': 'User Suspended',
                'message': f'User "{user.username}" has been suspended.',
                'type': 'error',
                'metadata': {'user_id': user.id, 'old_status': old_status},
            },
            user=actor or 'system',
            request=request
        )

        # Optionally revoke active sessions, etc.
        # from users.models.login_session import LoginSession
        # LoginSession.objects.filter(user=user, is_active=True).update(is_active=False)

        logger.info(f"[UserTransition] User #{user.id} suspended")

    @staticmethod
    def _on_delete(user, old_status, actor=None, request=None):
        """Handle soft delete."""
        log_audit_event(
            request=request,
            user=actor or user,
            action_type='delete',
            model_name='User',
            object_id=str(user.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'deleted'},
            }
        )

        NotificationService.create(
            data={
                'title': 'User Deleted',
                'message': f'User "{user.username}" has been deleted (soft delete).',
                'type': 'info',
                'metadata': {'user_id': user.id, 'old_status': old_status},
            },
            user=actor or 'system',
            request=request
        )

        logger.info(f"[UserTransition] User #{user.id} soft-deleted")