# users/handlers.py
import logging

from users.services.User import UserService

logger = logging.getLogger(__name__)


class UserStatusHandler:
    """Handles automated actions for user status changes."""

    @staticmethod
    def handle_user_status_change(instance, old_status, new_status, actor=None):
        if old_status == new_status:
            return

        logger.info(f"User {instance.pk} status changed: {old_status} → {new_status}")

        dispatch = {
            "active": lambda: UserService.activate_user(instance, actor, old_status=old_status),
            "restricted": lambda: UserService.restrict_user(instance, actor, old_status=old_status),
            "suspended": lambda: UserService.suspend_user(instance, actor, old_status=old_status),
            "deleted": lambda: UserService.soft_delete_user(instance, actor, old_status=old_status),
        }

        action = dispatch.get(new_status)
        if action:
            action()
        else:
            logger.warning(f"No handler for user status: {new_status}")