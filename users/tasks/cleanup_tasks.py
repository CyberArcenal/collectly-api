# users/tasks/cleanup_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.db.models import Q

from users.models.blacklisted_token import BlacklistedAccessToken
from users.models.login_checkpoint import LoginCheckpoint
from users.models.login_session import LoginSession
from users.models.otp_request import OtpRequest
from users.models.security_log import SecurityLog
from users.models.user_activity import UserActivity
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_expired_security_records(self, days: int = 30, user: str = 'system'):
    """
    Clean up expired security records.
    """
    logger.info("[SECURITY CLEANUP] Starting cleanup of expired security records...")

    try:
        now = timezone.now()
        cutoff = now - timedelta(days=days)

        results = {}

        # 1. Delete expired blacklisted tokens
        blacklisted_deleted, _ = BlacklistedAccessToken.objects.filter(
            expires_at__lt=now
        ).delete()
        results['deleted_blacklisted'] = blacklisted_deleted

        # 2. Delete expired login checkpoints
        checkpoints_deleted, _ = LoginCheckpoint.objects.filter(
            expires_at__lt=now
        ).exclude(is_used=True).delete()
        results['deleted_checkpoints'] = checkpoints_deleted

        # 3. Delete expired/inactive login sessions
        sessions_deleted, _ = LoginSession.objects.filter(
            Q(expires_at__lt=now) | Q(is_active=False),
            last_used__lt=cutoff
        ).delete()
        results['deleted_sessions'] = sessions_deleted

        # 4. Delete expired OTP requests
        otp_deleted, _ = OtpRequest.objects.filter(
            expires_at__lt=now
        ).exclude(is_used=True).delete()
        results['deleted_otp'] = otp_deleted

        # 5. Delete old security logs (older than days)
        security_logs_deleted, _ = SecurityLog.objects.filter(
            created_at__lt=cutoff
        ).delete()
        results['deleted_security_logs'] = security_logs_deleted

        # 6. Delete old user activities (older than days)
        activities_deleted, _ = UserActivity.objects.filter(
            timestamp__lt=cutoff
        ).delete()
        results['deleted_activities'] = activities_deleted

        total_deleted = sum(results.values())
        logger.info(f"[SECURITY CLEANUP] Deleted {total_deleted} expired security records")

        if total_deleted > 100:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🧹 Security Records Cleanup Completed",
                    message=f'Cleaned up {total_deleted} expired security records.',
                    type='info',
                    metadata=results,
                    user=user
                )
            except Exception as e:
                logger.warning(f"[SECURITY CLEANUP] Could not send notification: {e}")

        return results

    except Exception as e:
        logger.exception("[SECURITY CLEANUP] Cleanup failed")
        raise self.retry(exc=e, countdown=300)


@shared_task
def force_security_cleanup(days: int = 30, user: str = 'system'):
    """Wrapper for manual trigger of security cleanup."""
    logger.info("[SECURITY CLEANUP] 🔄 Force cleanup triggered")
    return cleanup_expired_security_records(days=days, user=user)