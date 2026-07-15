# audit/handlers.py
import logging

from audit.services.policy import AuditPolicyService

logger = logging.getLogger(__name__)


class AuditPolicyStatusHandler:
    """Handles automated actions for audit policy status changes"""

    @staticmethod
    def handle_policy_status_change(instance, old_status, new_status, user=None):
        if old_status == new_status:
            return

        logger.info(f"AuditPolicy {instance.id} changed: {old_status} → {new_status}")

        dispatch = {
            "enabled": lambda: AuditPolicyService.enable_policy(instance, user, old_status=old_status),
            "disabled": lambda: AuditPolicyService.disable_policy(instance, user, old_status=old_status),
            "deprecated": lambda: AuditPolicyService.deprecate_policy(instance, user, old_status=old_status),
        }

        action = dispatch.get(new_status)
        if action:
            action()
        else:
            logger.warning(f"No handler for audit policy status: {new_status}")