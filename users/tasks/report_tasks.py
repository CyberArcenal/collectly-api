# users/tasks/report_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from users.models.User import User
from users.models.login_session import LoginSession
from users.models.security_log import SecurityLog
from users.models.user_security_settings import UserSecuritySettings
from users.enums.base import UserRole, UserStatus
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_security_report(self, user: str = 'system'):
    """
    Send a weekly security report to admins.
    """
    logger.info("[SECURITY REPORT] Generating weekly security report...")

    try:
        now = timezone.now()
        week_ago = now - timedelta(days=7)

        total_users = User.objects.filter(is_deleted=False).count()
        active_users = User.objects.filter(is_deleted=False, status=UserStatus.ACTIVE).count()
        suspended_users = User.objects.filter(is_deleted=False, status=UserStatus.SUSPENDED).count()
        restricted_users = User.objects.filter(is_deleted=False, status=UserStatus.RESTRICTED).count()

        new_users = User.objects.filter(is_deleted=False, created_at__gte=week_ago).count()
        new_admins = User.objects.filter(
            is_deleted=False,
            user_type__in=[UserRole.ADMIN, UserRole.MANAGER],
            created_at__gte=week_ago
        ).count()

        total_logins = SecurityLog.objects.filter(
            event_type='login',
            created_at__gte=week_ago
        ).count()

        failed_logins = SecurityLog.objects.filter(
            event_type='failed_login',
            created_at__gte=week_ago
        ).count()

        unique_ips = SecurityLog.objects.filter(
            created_at__gte=week_ago
        ).values('ip_address').distinct().count()

        active_sessions = LoginSession.objects.filter(
            is_active=True,
            expires_at__gt=now
        ).count()

        password_changes = SecurityLog.objects.filter(
            event_type='password_change',
            created_at__gte=week_ago
        ).count()

        two_fa_enabled = UserSecuritySettings.objects.filter(
            two_factor_enabled=True
        ).count()

        report_data = {
            'generated_at': now.isoformat(),
            'period_days': 7,
            'users': {
                'total': total_users,
                'active': active_users,
                'suspended': suspended_users,
                'restricted': restricted_users,
                'new': new_users,
                'new_admins': new_admins,
            },
            'logins': {
                'total': total_logins,
                'failed': failed_logins,
                'unique_ips': unique_ips,
            },
            'sessions': {
                'active': active_sessions,
            },
            'security': {
                'password_changes': password_changes,
                'two_fa_enabled': two_fa_enabled,
            },
            'suspicious_activity': {
                'suspicious_ips': 0,
                'suspicious_users': 0,
            }
        }

        try:
            NotificationService.notify_admins_and_staff(
                title="📊 Weekly Security Report",
                message=f'Security report for the past week: '
                        f'{new_users} new users, '
                        f'{failed_logins} failed login attempts.',
                type='info',
                metadata=report_data,
                user=user
            )
        except Exception as e:
            logger.warning(f"[SECURITY REPORT] Could not send notification: {e}")

        logger.info("[SECURITY REPORT] Report generated and sent")
        return report_data

    except Exception as e:
        logger.exception("[SECURITY REPORT] Report generation failed")
        raise self.retry(exc=e, countdown=300)


@shared_task
def force_security_report(user: str = 'system'):
    """Wrapper for manual trigger of security report."""
    logger.info("[SECURITY REPORT] 🔄 Force report triggered")
    return send_security_report(user=user)