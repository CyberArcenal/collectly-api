# users/tasks/user.py
import logging
from datetime import timedelta
from typing import Optional, List, Dict, Any

from celery import shared_task
from django.db import transaction
from django.db.models import Q, Count, F
from django.utils import timezone
from django.conf import settings

from users.models.User import User
from users.models.blacklisted_token import BlacklistedAccessToken
from users.models.login_checkpoint import LoginCheckpoint
from users.models.login_session import LoginSession
from users.models.otp_request import OtpRequest
from users.models.security_log import SecurityLog
from users.models.user_activity import UserActivity
from users.models.user_security_settings import UserSecuritySettings
from users.enums.base import UserRole, UserStatus
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


# ============================================================
# CLEANUP TASKS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_expired_security_records(self, days: int = 30, user: str = 'system'):
    """
    Clean up expired security records.

    Deletes:
    - Expired blacklisted tokens
    - Expired login checkpoints
    - Expired login sessions (inactive)
    - Expired OTP requests
    - Old security logs and user activities

    Args:
        days: Number of days to keep logs and activities (default 30)
        user: User performing the cleanup

    Returns:
        dict: {
            'deleted_blacklisted': int,
            'deleted_checkpoints': int,
            'deleted_sessions': int,
            'deleted_otp': int,
            'deleted_security_logs': int,
            'deleted_activities': int
        }
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

        # Send notification if substantial cleanup happened
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


# ============================================================
# USER ACTIVITY MONITORING
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def check_suspicious_activity(self, threshold: int = 5, time_window_hours: int = 24):
    """
    Check for suspicious user activity patterns.

    Looks for:
    - Multiple failed login attempts from same IP
    - Multiple failed logins for same user in a short period
    - Logins from unusual locations (if location data is available)

    Args:
        threshold: Number of failed attempts to trigger alert
        time_window_hours: Time window in hours to consider

    Returns:
        dict: {
            'suspicious_ips': list,
            'suspicious_users': list,
            'alerts_sent': int
        }
    """
    logger.info("[SECURITY MONITOR] Checking for suspicious activity...")

    try:
        from django.db.models import Count
        from django.db import connection

        time_window = timezone.now() - timedelta(hours=time_window_hours)

        # 1. Find IPs with many failed logins
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

        # 2. Find users with many failed logins
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

        # Send alerts if suspicious patterns found
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


# ============================================================
# AUTO-SUSPEND INACTIVE USERS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_suspend_inactive_users(self, days: int = 90, user: str = 'system'):
    """
    Automatically suspend users who haven't logged in for N days.

    Args:
        days: Number of days of inactivity before suspension
        user: User performing the action

    Returns:
        dict: {
            'suspended': int,
            'skipped': int,
            'errors': list
        }
    """
    logger.info(f"[USER MANAGEMENT] Auto-suspending users inactive for > {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find users who haven't logged in since cutoff
        users_to_suspend = User.objects.filter(
            is_deleted=False,
            status=UserStatus.ACTIVE,
            last_login__lt=cutoff,
            user_type__in=[UserRole.STAFF, UserRole.VIEWER, UserRole.COLLECTOR]  # Skip admins/managers
        )

        suspended_count = 0
        skipped_count = 0
        errors = []

        for user_obj in users_to_suspend:
            try:
                # Check if user has any active login session more recent than cutoff
                # If they have a session, they've logged in recently
                has_active_session = LoginSession.objects.filter(
                    user=user_obj,
                    is_active=True,
                    last_used__gte=cutoff
                ).exists()

                if has_active_session:
                    skipped_count += 1
                    continue

                # Suspend the user
                with transaction.atomic():
                    user_obj.status = UserStatus.SUSPENDED
                    user_obj.save(update_fields=['status', 'updated_at'])

                    # Log the action
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

                    # Deactivate all login sessions
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

        # Send notification
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


# ============================================================
# SECURITY REPORT TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_security_report(self, user: str = 'system'):
    """
    Send a weekly security report to admins.

    Includes:
    - New user registrations
    - Failed login attempts
    - Suspended/deleted users
    - Active sessions count
    - Security log summary

    Returns:
        dict: Report data
    """
    logger.info("[SECURITY REPORT] Generating weekly security report...")

    try:
        now = timezone.now()
        week_ago = now - timedelta(days=7)

        # Statistics
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

        # Login statistics
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

        # Session statistics
        active_sessions = LoginSession.objects.filter(
            is_active=True,
            expires_at__gt=now
        ).count()

        # Password changes
        password_changes = SecurityLog.objects.filter(
            event_type='password_change',
            created_at__gte=week_ago
        ).count()

        # 2FA stats
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
                'suspicious_ips': 0,  # placeholder, could call the monitoring task
                'suspicious_users': 0,
            }
        }

        # Send report to admins
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


# ============================================================
# USER CLEANUP TASK (orphaned users)
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_orphaned_users(self, days: int = 30, user: str = 'system'):
    """
    Clean up users that were created but never verified (no login, no activity).

    Args:
        days: Days since creation to consider orphaned
        user: User performing the action

    Returns:
        dict: {
            'deleted': int,
            'skipped': int,
            'errors': list
        }
    """
    logger.info(f"[USER MANAGEMENT] Cleaning up orphaned users (created > {days} days, no login)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find users with no login and no activity
        orphaned_users = User.objects.filter(
            is_deleted=False,
            status=UserStatus.ACTIVE,
            created_at__lt=cutoff,
            last_login__isnull=True,
            user_type__in=[UserRole.VIEWER, UserRole.CUSTOMER]  # Skip staff/admin
        )

        deleted_count = 0
        skipped_count = 0
        errors = []

        for user_obj in orphaned_users:
            try:
                # Check if user has any security logs (might indicate activity)
                has_activity = SecurityLog.objects.filter(
                    user=user_obj
                ).exists()

                if has_activity:
                    skipped_count += 1
                    continue

                # Soft delete the user
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

        # Send notification
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


# ============================================================
# FORCE TASKS (manual triggers)
# ============================================================

@shared_task
def force_security_cleanup(days: int = 30, user: str = 'system'):
    """Wrapper for manual trigger of security cleanup."""
    logger.info("[SECURITY CLEANUP] 🔄 Force cleanup triggered")
    return cleanup_expired_security_records(days=days, user=user)


@shared_task
def force_security_monitor(user: str = 'system'):
    """Wrapper for manual trigger of suspicious activity check."""
    logger.info("[SECURITY MONITOR] 🔄 Force monitor triggered")
    return check_suspicious_activity()


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


@shared_task
def force_security_report(user: str = 'system'):
    """Wrapper for manual trigger of security report."""
    logger.info("[SECURITY REPORT] 🔄 Force report triggered")
    return send_security_report(user=user)