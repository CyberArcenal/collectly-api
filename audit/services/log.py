# audit/services/log.py
import logging
from django.db import transaction
from django.utils import timezone
from audit.models.log import AuditLog
from audit.models.policy import AuditPolicy

logger = logging.getLogger(__name__)


class AuditLogService:
    """
    Service layer for AuditLog operations.
    Handles log creation, retrieval, and management.
    """

    # ======================================================================
    # GETTERS
    # ======================================================================

    @staticmethod
    def get_log_by_id(log_id: int) -> AuditLog:
        """
        Get an audit log by its primary key.

        Args:
            log_id: The log ID.

        Returns:
            AuditLog: The log instance.

        Raises:
            AuditLog.DoesNotExist: If log not found.
        """
        return AuditLog.objects.get(id=log_id)

    @staticmethod
    def get_log_by_event_id(event_id: str) -> AuditLog:
        """
        Get an audit log by its event UUID.

        Args:
            event_id: The event UUID.

        Returns:
            AuditLog: The log instance.

        Raises:
            AuditLog.DoesNotExist: If log not found.
        """
        return AuditLog.objects.get(event_id=event_id)

    @staticmethod
    def get_logs_by_user(user_id: int, limit: int = 100) -> list:
        """
        Get audit logs for a specific user.

        Args:
            user_id: The user ID.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(user_id=user_id).order_by('-timestamp')[:limit]

    @staticmethod
    def get_logs_by_action_type(action_type: str, limit: int = 100) -> list:
        """
        Get audit logs by action type.

        Args:
            action_type: The action type.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(action_type=action_type).order_by('-timestamp')[:limit]

    @staticmethod
    def get_suspicious_logs(limit: int = 50) -> list:
        """
        Get suspicious audit logs.

        Args:
            limit: Maximum number of logs to return.

        Returns:
            list: List of suspicious AuditLog objects.
        """
        return AuditLog.objects.filter(
            is_suspicious=True
        ).order_by('-timestamp')[:limit]

    @staticmethod
    def get_logs_by_date_range(start_date, end_date, limit: int = 100) -> list:
        """
        Get audit logs within a date range.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).order_by('-timestamp')[:limit]

    # ======================================================================
    # CRUD OPERATIONS
    # ======================================================================

    @staticmethod
    @transaction.atomic
    def create_log(validated_data: dict) -> AuditLog:
        """
        Create a new audit log entry.

        Args:
            validated_data: Validated data from the serializer.

        Returns:
            AuditLog: The created log instance.

        Note:
            AuditLog entries are immutable and cannot be updated or deleted.
        """
        # Set timestamp to now if not provided
        if "timestamp" not in validated_data:
            validated_data["timestamp"] = timezone.now()

        log_entry = AuditLog.objects.create(**validated_data)

        logger.info(
            f"AuditLog created: event_id={log_entry.event_id}, "
            f"action={log_entry.action_type}, "
            f"model={log_entry.model_name}, "
            f"user={log_entry.user_id or 'System'}"
        )

        # Check retention policy and archive if needed
        AuditLogService._check_retention_policy(log_entry)

        return log_entry

    @staticmethod
    def _check_retention_policy(log_entry: AuditLog):
        """
        Check if log should be archived based on retention policy.

        Args:
            log_entry: The new AuditLog entry.
        """
        try:
            policy = AuditPolicy.objects.first()
            if policy:
                # Check if log is suspicious and should be flagged
                # This could trigger additional monitoring
                if log_entry.is_suspicious:
                    logger.warning(
                        f"Suspicious activity detected: {log_entry.action_type} "
                        f"on {log_entry.model_name} by {log_entry.user_id or 'System'}"
                    )
        except Exception as e:
            logger.error(f"Failed to check retention policy: {e}")

    # ======================================================================
    # STATISTICS
    # ======================================================================

    @staticmethod
    def get_log_statistics(days: int = 7) -> dict:
        """
        Get audit log statistics.

        Args:
            days: Number of days to look back.

        Returns:
            dict: Statistics about audit logs.
        """
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta

        start_date = timezone.now() - timedelta(days=days)

        total_logs = AuditLog.objects.filter(timestamp__gte=start_date).count()

        # Action type distribution
        action_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).values('action_type').annotate(
            count=Count('id')
        ).order_by('-count')

        # Model name distribution
        model_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).values('model_name').annotate(
            count=Count('id')
        ).order_by('-count')

        # Suspicious count
        suspicious_count = AuditLog.objects.filter(
            timestamp__gte=start_date,
            is_suspicious=True
        ).count()

        # User distribution
        user_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date,
            user__isnull=False
        ).values('user__username').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        return {
            "total_logs": total_logs,
            "suspicious_count": suspicious_count,
            "days": days,
            "action_distribution": list(action_distribution),
            "model_distribution": list(model_distribution),
            "top_users": list(user_distribution),
        }

    @staticmethod
    def get_log_count_by_date(days: int = 7) -> list:
        """
        Get daily log counts for the specified period.

        Args:
            days: Number of days to look back.

        Returns:
            list: Daily log counts.
        """
        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.utils import timezone
        from datetime import timedelta

        start_date = timezone.now() - timedelta(days=days)

        daily_counts = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).annotate(
            date=TruncDate('timestamp')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')

        return list(daily_counts)

    # ======================================================================
    # CLEANUP (Admin only)
    # ======================================================================

    @staticmethod
    @transaction.atomic
    def cleanup_old_logs(days: int = 90) -> int:
        """
        Delete audit logs older than the specified days.

        Args:
            days: Number of days to keep.

        Returns:
            int: Number of logs deleted.

        Warning:
            This is irreversible. Should only be done by admins.
        """
        from django.utils import timezone
        from datetime import timedelta

        # Check retention policy
        policy = AuditPolicy.objects.first()
        if policy and days < policy.retention_years * 365:
            raise ValueError(
                f"Cannot delete logs older than {days} days. "
                f"Retention policy requires {policy.retention_years} years."
            )

        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = AuditLog.objects.filter(
            timestamp__lt=cutoff_date
        ).delete()

        logger.info(f"Deleted {deleted_count} audit logs older than {days} days")

        return deleted_count