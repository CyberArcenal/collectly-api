import logging
from audit.models.log import AuditLog
from audit.models.policy import AuditPolicy

logger = logging.getLogger(__name__)
class AuditService:
    """
    Service layer for audit app.
    Provides abstraction for AuditLog and AuditPolicy operations.
    """

    # -------------------------------
    # AuditLog methods
    # -------------------------------
    @staticmethod
    def get_log_by_id(log_id):
        try:
            return AuditLog.objects.filter(id=log_id).first()
        except Exception as exc:
            logger.error(f"Error retrieving AuditLog {log_id}: {exc}")
            return None

    @staticmethod
    def search_logs():
        return AuditLog.objects.all()

    @staticmethod
    def create_log(**kwargs):
        try:
            log_obj = AuditLog.objects.create(**kwargs)
            return log_obj
        except Exception as exc:
            logger.error(f"Error creating AuditLog: {exc}")
            raise

    @staticmethod
    def update_log(log_obj, user, **kwargs):
        try:
            for field, value in kwargs.items():
                setattr(log_obj, field, value)
            log_obj.save(update_fields=list(kwargs.keys()))
            return log_obj
        except Exception as exc:
            logger.error(f"Error updating AuditLog {log_obj.id}: {exc}")
            raise

    @staticmethod
    def delete_log(log_obj, user):
        try:
            log_obj.delete()
        except Exception as exc:
            logger.error(f"Error deleting AuditLog {log_obj.id}: {exc}")
            raise

    # -------------------------------
    # AuditPolicy methods
    # -------------------------------
    @staticmethod
    def get_policy_by_id(policy_id):
        try:
            return AuditPolicy.objects.filter(id=policy_id).first()
        except Exception as exc:
            logger.error(f"Error retrieving AuditPolicy {policy_id}: {exc}")
            return None

    @staticmethod
    def search_policies():
        return AuditPolicy.objects.all()

    @staticmethod
    def create_policy(**kwargs):
        try:
            policy_obj = AuditPolicy.objects.create(**kwargs)
            return policy_obj
        except Exception as exc:
            logger.error(f"Error creating AuditPolicy: {exc}")
            raise

    @staticmethod
    def update_policy(policy_obj, user, **kwargs):
        try:
            for field, value in kwargs.items():
                setattr(policy_obj, field, value)
            policy_obj.save(update_fields=list(kwargs.keys()))
            return policy_obj
        except Exception as exc:
            logger.error(f"Error updating AuditPolicy {policy_obj.id}: {exc}")
            raise

    @staticmethod
    def delete_policy(policy_obj, user):
        try:
            policy_obj.delete()
        except Exception as exc:
            logger.error(f"Error deleting AuditPolicy {policy_obj.id}: {exc}")
            raise