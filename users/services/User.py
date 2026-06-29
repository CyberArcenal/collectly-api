# users/services/base.py
import logging
from django.db import transaction

from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class UserService:
    """Service methods triggered by UserStatusHandler after status change."""

    @staticmethod
    def activate_user(instance, actor=None, old_status=None):
        with transaction.atomic():
            log_audit_event(
                request=None,
                user=actor or instance,
                action_type="update",
                model_name="User",
                object_id=str(instance.pk),
                changes={"before": {"status": old_status}, "after": {"status": "active"}},
            )
            logger.info(f"User {instance.pk} activated by {actor}")

    @staticmethod
    def restrict_user(instance, actor=None, old_status=None):
        with transaction.atomic():
            log_audit_event(
                request=None,
                user=actor or instance,
                action_type="update",
                model_name="User",
                object_id=str(instance.pk),
                changes={"before": {"status": old_status}, "after": {"status": "restricted"}},
            )
            logger.info(f"User {instance.pk} restricted by {actor}")

    @staticmethod
    def suspend_user(instance, actor=None, old_status=None):
        with transaction.atomic():
            log_audit_event(
                request=None,
                user=actor or instance,
                action_type="update",
                model_name="User",
                object_id=str(instance.pk),
                changes={"before": {"status": old_status}, "after": {"status": "suspended"}},
            )
            logger.info(f"User {instance.pk} suspended by {actor}")

    @staticmethod
    def soft_delete_user(instance, actor=None, old_status=None):
        with transaction.atomic():
            log_audit_event(
                request=None,
                user=actor or instance,
                action_type="delete",
                model_name="User",
                object_id=str(instance.pk),
                changes={"before": {"status": old_status}, "after": {"status": "deleted"}},
            )
            logger.info(f"User {instance.pk} soft-deleted by {actor}")