# users/tasks/user_management_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from users.models.User import User
from users.models.login_session import LoginSession
from users.models.security_log import SecurityLog
from users.enums.base import UserRole, UserStatus
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_suspend_inactive_users(self, days: int = 90, user: str = 'system'):
    """
    Automatically suspend users who haven't logged in for N days.
    """
    logger.info(f"[USER MANAGEMENT] Auto-suspending users inactive for > {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        users_to_suspend = User.objects.filter(
            is_deleted=False,
            status=UserStatus.ACTIVE,
            last_login__lt=cutoff,
            user_type__in=[UserRole.STAFF, UserRole.VIEWER, UserRole.COLLECTOR]
        )

        suspended_count = 0
        skipped_count = 0
        errors = []

        for user_obj in users_to_suspend:
            try:
                has_active_session = LoginSession.objects.filter(
                    user=user_obj,
                    is_active=True,
                    last_used__gte=cutoff
                ).exists()

                if has_active_session:
                    skipped_count += 1
                    continue

                with transaction.atomic():
                    user_obj.status = UserStatus.SUSPENDED
                    user_obj.save(update_fields=['status', 'updated_at'])

                    log_audit_event(
                        request=None,
                        user=user_obj,
                        action_type='auto_suspend',
                        model_name='User',
                        object_id=str(user_obj.id),
                        changes={
                            'reason': f'Inactive for {days} days',
                            'last_login': user_obj.last_login,
                        }
                    )

                    LoginSession.objects.filter(
                        user=user_obj,
                        is_active=True
                    ).update(is_active=False)

                    suspended_count += 1
                    logger.info(f"[USER MANAGEMENT] Auto-suspended user #{user_obj.id}: {user_obj.username}")

            except Exception as e:
                errors.append({
                    'user_id': user_obj.id,
                    'username': user_obj.username,
                    'error': str(e)
                })
                logger.error(f"[USER MANAGEMENT] Failed to suspend user #{user_obj.id}: {e}")

        if suspended_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Auto-Suspension Completed",
                    message=f'Suspended {suspended_count} inactive users (inactive > {days} days). {skipped_count} skipped.',
                    type='warning',
                    metadata={
                        'suspended': suspended_count,
                        'skipped': skipped_count,
                        'errors': errors[:5],
                        'days': days
                    },
                    user=user
                )
            except Exception as e:
                logger.warning(f"[USER MANAGEMENT] Could not send notification: {e}")

        return {
            'suspended': suspended_count,
            'skipped': skipped_count,
            'errors': errors,
            'total_checked': users_to_suspend.count()
        }

    except Exception as e:
        logger.exception("[USER MANAGEMENT] Auto-suspend failed")
        raise self.retry(exc=e, countdown=300)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_orphaned_users(self, days: int = 30, user: str = 'system'):
    """
    Clean up users that were created but never verified (no login, no activity).
    """
    logger.info(f"[USER MANAGEMENT] Cleaning up orphaned users (created > {days} days, no login)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        orphaned_users = User.objects.filter(
            is_deleted=False,
            status=UserStatus.ACTIVE,
            created_at__lt=cutoff,
            last_login__isnull=True,
            user_type__in=[UserRole.VIEWER, UserRole.CUSTOMER]
        )

        deleted_count = 0
        skipped_count = 0
        errors = []

        for user_obj in orphaned_users:
            try:
                has_activity = SecurityLog.objects.filter(
                    user=user_obj
                ).exists()

                if has_activity:
                    skipped_count += 1
                    continue

                with transaction.atomic():
                    user_obj.status = UserStatus.DELETED
                    user_obj.is_deleted = True
                    user_obj.save(update_fields=['status', 'is_deleted', 'updated_at'])

                    log_audit_event(
                        request=None,
                        user=user_obj,
                        action_type='orphan_cleanup',
                        model_name='User',
                        object_id=str(user_obj.id),
                        changes={
                            'reason': f'Orphaned user, no login for {days} days',
                            'created_at': user_obj.created_at,
                        }
                    )

                    deleted_count += 1
                    logger.info(f"[USER MANAGEMENT] Cleaned up orphaned user #{user_obj.id}: {user_obj.username}")

            except Exception as e:
                errors.append({
                    'user_id': user_obj.id,
                    'username': user_obj.username,
                    'error': str(e)
                })
                logger.error(f"[USER MANAGEMENT] Failed to clean up orphaned user #{user_obj.id}: {e}")

        if deleted_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🧹 Orphaned User Cleanup Completed",
                    message=f'Deleted {deleted_count} orphaned users (no login for > {days} days). {skipped_count} skipped.',
                    type='info',
                    metadata={
                        'deleted': deleted_count,
                        'skipped': skipped_count,
                        'errors': errors[:5],
                        'days': days
                    },
                    user=user
                )
            except Exception as e:
                logger.warning(f"[USER MANAGEMENT] Could not send notification: {e}")

        return {
            'deleted': deleted_count,
            'skipped': skipped_count,
            'errors': errors,
            'total_checked': orphaned_users.count()
        }

    except Exception as e:
        logger.exception("[USER MANAGEMENT] Orphaned cleanup failed")
        raise self.retry(exc=e, countdown=300)


@shared_task
def force_suspend_inactive(days: int = 90, user: str = 'system'):
    """Wrapper for manual trigger of auto-suspend."""
    logger.info("[USER MANAGEMENT] 🔄 Force auto-suspend triggered")
    return auto_suspend_inactive_users(days=days, user=user)


@shared_task
def force_orphan_cleanup(days: int = 30, user: str = 'system'):
    """Wrapper for manual trigger of orphaned user cleanup."""
    logger.info("[USER MANAGEMENT] 🔄 Force orphan cleanup triggered")
    return cleanup_orphaned_users(days=days, user=user)