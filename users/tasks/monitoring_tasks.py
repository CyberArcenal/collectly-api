# users/tasks/monitoring_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.db.models import Count

from users.models.security_log import SecurityLog
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def check_suspicious_activity(self, threshold: int = 5, time_window_hours: int = 24):
    """
    Check for suspicious user activity patterns.
    """
    logger.info("[SECURITY MONITOR] Checking for suspicious activity...")

    try:
        time_window = timezone.now() - timedelta(hours=time_window_hours)

        suspicious_ips = (
            SecurityLog.objects.filter(
                event_type='failed_login',
                created_at__gte=time_window
            )
            .values('ip_address')
            .annotate(failed_count=Count('id'))
            .filter(failed_count__gte=threshold)
            .order_by('-failed_count')
        )

        suspicious_users = (
            SecurityLog.objects.filter(
                event_type='failed_login',
                created_at__gte=time_window
            )
            .values('user_id')
            .annotate(failed_count=Count('id'))
            .filter(failed_count__gte=threshold)
            .order_by('-failed_count')
        )

        alerts_sent = 0
        result = {
            'suspicious_ips': list(suspicious_ips),
            'suspicious_users': list(suspicious_users),
            'alerts_sent': 0
        }

        if suspicious_ips or suspicious_users:
            message_parts = []
            if suspicious_ips:
                message_parts.append(
                    f"{len(suspicious_ips)} IP(s) with {threshold}+ failed logins"
                )
            if suspicious_users:
                message_parts.append(
                    f"{len(suspicious_users)} user(s) with {threshold}+ failed logins"
                )

            try:
                NotificationService.notify_admins_and_staff(
                    title="⚠️ Suspicious Activity Detected",
                    message=f'Alert: {", ".join(message_parts)} in the last {time_window_hours} hours.',
                    type='error',
                    metadata={
                        'suspicious_ips': list(suspicious_ips)[:10],
                        'suspicious_users': list(suspicious_users)[:10],
                        'threshold': threshold,
                        'time_window_hours': time_window_hours
                    },
                    user='system'
                )
                alerts_sent = 1
                result['alerts_sent'] = alerts_sent
            except Exception as e:
                logger.warning(f"[SECURITY MONITOR] Could not send notification: {e}")

        logger.info(f"[SECURITY MONITOR] Completed: {len(suspicious_ips)} suspicious IPs, {len(suspicious_users)} suspicious users")

        return result

    except Exception as e:
        logger.exception("[SECURITY MONITOR] Monitoring failed")
        raise self.retry(exc=e, countdown=300)


@shared_task
def force_security_monitor(user: str = 'system'):
    """Wrapper for manual trigger of suspicious activity check."""
    logger.info("[SECURITY MONITOR] 🔄 Force monitor triggered")
    return check_suspicious_activity()