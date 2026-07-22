import logging
from datetime import timedelta
from django.utils import timezone
from django.db import transaction

from celery import shared_task
from django.core.cache import cache

from audit.models.log import AuditLog
from notifications.services.notification import NotificationService
from system_settings.utils import audit_log_enabled, log_retention_days

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def cleanup_old_audit_trails(self):
    """
    Celery task to clean up old audit trails based on retention policy.
    
    This task runs on a schedule (default: daily at 2 AM) and deletes
    audit logs older than the configured retention period.
    
    Returns:
        dict: {
            'status': str,
            'deleted_count': int,
            'retention_days': int,
            'cutoff_date': str,
            'message': str,
        }
    """
    logger.info("[AUDIT CLEANUP] Starting audit trail cleanup task...")

    try:
        # Check if audit log is enabled
        if not audit_log_enabled():
            logger.info("[AUDIT CLEANUP] Audit log is disabled, skipping cleanup")
            return {
                'status': 'skipped',
                'message': 'Audit log is disabled in system settings',
                'deleted_count': 0,
            }

        # Get retention days from system settings
        retention_days = log_retention_days()
        cutoff_date = timezone.now() - timedelta(days=retention_days)

        logger.info(
            f"[AUDIT CLEANUP] Cleaning up audit trails older than {retention_days} days "
            f"(before {cutoff_date.isoformat()})"
        )

        # Count records to delete
        count_to_delete = AuditLog.objects.filter(
            timestamp__lt=cutoff_date
        ).count()

        if count_to_delete == 0:
            logger.info("[AUDIT CLEANUP] No old audit trail records to delete")
            return {
                'status': 'completed',
                'message': 'No old records to delete',
                'deleted_count': 0,
                'retention_days': retention_days,
                'cutoff_date': cutoff_date.isoformat(),
            }

        # Delete old records
        with transaction.atomic():
            deleted_count, _ = AuditLog.objects.filter(
                timestamp__lt=cutoff_date
            ).delete()

        logger.info(
            f"[AUDIT CLEANUP] ✅ Deleted {deleted_count} audit trail records "
            f"older than {retention_days} days"
        )

        if deleted_count > 0:
            try:
                from notifications.services.notification import NotificationService
                NotificationService.notify_admins_and_staff(
                    title='Audit Log Cleanup Completed',
                    message=f'{deleted_count} old audit record(s) older than {retention_days} days have been deleted.',
                    type='info',
                    metadata={
                        'deleted_count': deleted_count,
                        'retention_days': retention_days,
                        'cutoff_date': cutoff_date.isoformat(),
                    },
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[AUDIT CLEANUP] Could not send notification: {e}")

        # Log the cleanup action (as an audit log entry itself)
        try:
            AuditLog.objects.create(
                user=None,
                action_type='system_alert',
                model_name='AuditTrail',
                object_id='cleanup',
                changes={
                    'action': 'AUDIT_CLEANUP',
                    'retention_days': retention_days,
                    'deleted_count': deleted_count,
                    'cutoff_date': cutoff_date.isoformat(),
                },
                ip_address=None,
                user_agent=None,
                is_suspicious=False,
            )
        except Exception as e:
            logger.warning(f"[AUDIT CLEANUP] Could not log cleanup action: {e}")

        # Store cleanup stats in cache for quick access
        cache.set(
            'audit_cleanup_last_run',
            {
                'timestamp': timezone.now().isoformat(),
                'deleted_count': deleted_count,
                'retention_days': retention_days,
                'cutoff_date': cutoff_date.isoformat(),
            },
            timeout=86400  # 24 hours
        )

        return {
            'status': 'completed',
            'deleted_count': deleted_count,
            'retention_days': retention_days,
            'cutoff_date': cutoff_date.isoformat(),
            'message': f'Deleted {deleted_count} old audit records',
        }

    except Exception as e:
        logger.error(f"[AUDIT CLEANUP] ❌ Error during audit trail cleanup: {e}")
        
        # Send failure notification
        try:
            NotificationService.create(
                data={
                    'title': 'Audit Log Cleanup Failed',
                    'message': f'Failed to clean up old audit logs: {str(e)}',
                    'type': 'error',
                    'metadata': {'error': str(e)},
                },
                user='system',
                request=None
            )
        except Exception as notif_err:
            logger.warning(f"[AUDIT CLEANUP] Could not send failure notification: {notif_err}")

        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@shared_task
def get_audit_cleanup_stats():
    """
    Get statistics about audit logs and cleanup status.
    
    Returns:
        dict: {
            'total_records': int,
            'old_records_to_delete': int,
            'retention_days': int,
            'cutoff_date': str,
            'oldest_record_date': str or None,
            'last_run': dict or None,
        }
    """
    try:
        retention_days = log_retention_days()
        cutoff_date = timezone.now() - timedelta(days=retention_days)

        total_count = AuditLog.objects.count()
        old_records_count = AuditLog.objects.filter(
            timestamp__lt=cutoff_date
        ).count()

        oldest_record = AuditLog.objects.order_by('timestamp').first()
        last_run = cache.get('audit_cleanup_last_run')

        return {
            'total_records': total_count,
            'old_records_to_delete': old_records_count,
            'retention_days': retention_days,
            'cutoff_date': cutoff_date.isoformat(),
            'oldest_record_date': oldest_record.timestamp.isoformat() if oldest_record else None,
            'last_run': last_run,
            'cleanup_enabled': audit_log_enabled(),
        }
    except Exception as e:
        logger.error(f"[AUDIT CLEANUP] Error getting cleanup stats: {e}")
        return {
            'error': str(e),
            'total_records': 0,
            'old_records_to_delete': 0,
        }


@shared_task
def force_audit_cleanup():
    """
    Force immediate audit trail cleanup.
    This is used for manual triggers from admin panel.
    """
    logger.info("[AUDIT CLEANUP] 🔄 Force audit trail cleanup triggered")
    return cleanup_old_audit_trails()