# audit/loggers/status_change.py
import logging

logger = logging.getLogger(__name__)

# audit/router.py
import logging
# future: from audit.loggers.security import SecurityLogger, etc.

logger = logging.getLogger(__name__)


            
            
class StatusChangeLogger:
    """
    Handles all audit events with action='status_change'.
    Centralized processor for status transitions across entities.
    """

    @staticmethod
    def process(log_entry):
        """
        Process a status change audit log entry.
        :param log_entry: AuditLog instance created by log_audit_event
        """
        details = log_entry.details or {}
        old_status = details.get("old_status")
        new_status = details.get("new_status")

        logger.info(
            f"[AUDIT] {log_entry.entity}({log_entry.entity_id}) "
            f"status changed {old_status} → {new_status} by {log_entry.user}"
        )

        # Optional: add entity-specific hooks
        if log_entry.entity == "License":
            StatusChangeLogger._handle_license(details)
        elif log_entry.entity == "Transaction":
            StatusChangeLogger._handle_transaction(details)
        elif log_entry.entity == "NotificationEvent":
            StatusChangeLogger._handle_notification(details)
        elif log_entry.entity == "AuditPolicy":
            StatusChangeLogger._handle_policy(details)
        elif log_entry.entity == "DeviceBinding":
            StatusChangeLogger._handle_device(details)

    @staticmethod
    def _handle_license(details):
        logger.debug(f"License status change details: {details}")

    @staticmethod
    def _handle_transaction(details):
        logger.debug(f"Transaction status change details: {details}")

    @staticmethod
    def _handle_notification(details):
        logger.debug(f"NotificationEvent status change details: {details}")

    @staticmethod
    def _handle_policy(details):
        logger.debug(f"AuditPolicy status change details: {details}")

    @staticmethod
    def _handle_device(details):
        logger.debug(f"DeviceBinding status change details: {details}")
        

class AuditLogRouter:
    """
    Central dispatcher for audit log entries.
    Routes based on log_entry.action to the appropriate logger class.
    """

    _registry = {
        "status_change": StatusChangeLogger,
        # "security_event": SecurityLogger,
        # "login_attempt": LoginLogger,
        # etc...
    }

    @staticmethod
    def dispatch(log_entry):
        """
        Dispatch an AuditLog entry to the appropriate logger class.
        """
        action = getattr(log_entry, "action", None)
        if not action:
            logger.warning(f"AuditLog {log_entry.id} has no action defined")
            return

        handler_cls = AuditLogRouter._registry.get(action)
        if handler_cls:
            handler_cls.process(log_entry)
        else:
            logger.info(f"No logger registered for action '{action}'")