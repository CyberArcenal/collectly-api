# loan_applications/tasks/application.py
import logging
from datetime import timedelta
from typing import Optional, List, Dict, Any

from celery import shared_task
from django.db import transaction
from django.db.models import Q, Count, Sum, Case, When, Value, IntegerField
from django.core.exceptions import ValidationError
from django.utils import timezone

from loan_applications.models.loan_application import LoanApplication
from loan_applications.services.loan_application import LoanApplicationService
from loan_applications.state_transitions.loan_application import LoanApplicationStateTransitionService
from borrowers.services.credit_check import CreditCheckService
from debts.services.debt import DebtService
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


# ============================================================
# AUTO-APPROVE PENDING APPLICATIONS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_approve_applications(self, limit: int = 50, user: str = 'system'):
    """
    Automatically approve pending loan applications based on credit score thresholds.

    Rules:
    - Credit score >= min_credit_score_for_approval (e.g., 700)
    - Requested amount <= max_loan_amount
    - Valid credit check (within validity days)

    Args:
        limit: Maximum number of applications to process per run
        user: User performing the action (for audit)

    Returns:
        dict: {
            'approved': int,
            'rejected': int,
            'errors': list
        }
    """
    logger.info(f"[LOAN APPLICATION TASK] Starting auto-approval for pending applications...")

    try:
        from system_settings.utils import (
            min_credit_score_for_approval,
            max_loan_amount,
            enforce_credit_check,
            credit_check_validity_days,
        )

        # Get pending applications with valid debtor
        pending_apps = LoanApplication.objects.select_related('debtor').filter(
            status=LoanApplication.Status.PENDING,
            deleted_at__isnull=True,
            debtor__isnull=False,
            debtor__deleted_at__isnull=True,
        ).order_by('created_at')[:limit]

        total = pending_apps.count()
        logger.info(f"[LOAN APPLICATION TASK] Found {total} pending applications")

        if total == 0:
            return {
                'approved': 0,
                'rejected': 0,
                'errors': [],
                'message': 'No pending applications to process'
            }

        approved_count = 0
        rejected_count = 0
        errors = []

        min_score = min_credit_score_for_approval()
        max_amount = max_loan_amount()
        need_credit_check = enforce_credit_check()

        for app in pending_apps:
            try:
                # Skip if no debtor
                if not app.debtor_id:
                    rejected_count += 1
                    errors.append({
                        'application_id': app.id,
                        'error': 'No debtor associated with application'
                    })
                    continue

                # Skip if amount exceeds max
                if max_amount > 0 and app.requested_amount > max_amount:
                    rejected_count += 1
                    errors.append({
                        'application_id': app.id,
                        'error': f'Amount exceeds max loan amount (₱{max_amount:,.2f})'
                    })
                    continue

                # Credit check enforcement
                if need_credit_check and min_score > 0:
                    # Get latest credit check
                    latest_check = CreditCheckService.get_latest(app.debtor_id)

                    if not latest_check:
                        rejected_count += 1
                        errors.append({
                            'application_id': app.id,
                            'error': f'No credit check found for debtor ID {app.debtor_id}'
                        })
                        continue

                    # Check validity
                    validity_days = credit_check_validity_days()
                    check_date = latest_check.date_checked.date() if latest_check.date_checked else None
                    if check_date:
                        days_since_check = (timezone.now().date() - check_date).days
                        if days_since_check > validity_days:
                            rejected_count += 1
                            errors.append({
                                'application_id': app.id,
                                'error': f'Credit check too old ({days_since_check} days)'
                            })
                            continue

                    # Check score
                    if latest_check.score < min_score:
                        rejected_count += 1
                        errors.append({
                            'application_id': app.id,
                            'error': f'Credit score {latest_check.score} below minimum {min_score}'
                        })
                        continue

                # ✅ All checks passed - auto-approve
                service = LoanApplicationService()
                approved_app = service.approve(app.id, user=user, request=None)

                # Send notifications via state transition
                transition = LoanApplicationStateTransitionService()
                transition.on_approve(approved_app, user=user, request=None)

                approved_count += 1
                logger.info(f"[LOAN APPLICATION TASK] Auto-approved application #{app.id}")

            except Exception as e:
                rejected_count += 1
                errors.append({
                    'application_id': app.id,
                    'error': str(e)
                })
                logger.error(f"[LOAN APPLICATION TASK] Failed to process application #{app.id}: {e}")

        # Notify admins
        if approved_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title='🔄 Auto-Approval Task Completed',
                message=f'Auto-approved: {approved_count} applications, {rejected_count} rejected/failed.',
                type='info' if not errors else 'error',
                metadata={
                    'approved': approved_count,
                    'rejected': rejected_count,
                    'total': total,
                    'errors': errors[:10]  # Only first 10 errors
                },
                user=user
            )

        result = {
            'approved': approved_count,
            'rejected': rejected_count,
            'total': total,
            'errors': errors,
            'message': f'Approved {approved_count} applications, {rejected_count} failed'
        }

        logger.info(f"[LOAN APPLICATION TASK] Auto-approval completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Auto-approval failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


# ============================================================
# STALE APPLICATION CLEANUP
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_stale_applications(self, days: int = 30, user: str = 'system'):
    """
    Soft delete stale (old) pending or rejected applications.

    Args:
        days: Age in days for applications to be considered stale
        user: User performing the action

    Returns:
        dict: {
            'deleted_count': int,
            'errors': list
        }
    """
    logger.info(f"[LOAN APPLICATION TASK] Starting stale application cleanup (older than {days} days)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find stale pending and rejected applications
        stale_apps = LoanApplication.objects.filter(
            deleted_at__isnull=True,
            created_at__lt=cutoff,
            status__in=[LoanApplication.Status.PENDING, LoanApplication.Status.REJECTED]
        )

        count = stale_apps.count()
        if count == 0:
            return {
                'deleted_count': 0,
                'message': 'No stale applications found'
            }

        deleted_count = 0
        errors = []

        for app in stale_apps:
            try:
                app.soft_delete()
                deleted_count += 1
                logger.info(f"[LOAN APPLICATION TASK] Soft-deleted stale application #{app.id}")
            except Exception as e:
                errors.append({
                    'application_id': app.id,
                    'error': str(e)
                })
                logger.error(f"[LOAN APPLICATION TASK] Failed to delete application #{app.id}: {e}")

        # Notify admins
        if deleted_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title='🧹 Stale Application Cleanup Completed',
                message=f'Deleted {deleted_count} stale applications, {len(errors)} errors.',
                type='info' if not errors else 'error',
                metadata={
                    'deleted_count': deleted_count,
                    'errors': errors[:10]
                },
                user=user
            )

        return {
            'deleted_count': deleted_count,
            'errors': errors,
            'message': f'Deleted {deleted_count} stale applications'
        }

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Stale cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# PENDING APPLICATION REMINDER
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_pending_application_reminders(self, days: int = 7, user: str = 'system'):
    """
    Send reminders for pending applications that have been waiting for more than N days.

    Args:
        days: Number of days after which to send a reminder
        user: User performing the action

    Returns:
        dict: {
            'sent_count': int,
            'errors': list
        }
    """
    logger.info(f"[LOAN APPLICATION TASK] Sending reminders for pending applications older than {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find pending applications older than cutoff
        pending_old = LoanApplication.objects.select_related('debtor').filter(
            status=LoanApplication.Status.PENDING,
            deleted_at__isnull=True,
            created_at__lt=cutoff,
            debtor__email__isnull=False,
            debtor__deleted_at__isnull=True,
        )

        sent_count = 0
        errors = []

        for app in pending_old:
            try:
                # Send email notification to debtor
                if app.debtor and app.debtor.email:
                    from notifications.tasks.reminder import send_email_task
                    from notifications.email_templates.loan_status import generate_pending_reminder_email

                    email_data = {
                        'applicant_name': app.debtor_name,
                        'application_id': app.id,
                        'purpose': app.purpose,
                        'amount': app.requested_amount,
                        'days_waiting': (timezone.now().date() - app.created_at.date()).days,
                    }

                    html = generate_pending_reminder_email(email_data)
                    send_email_task.delay(
                        to=app.debtor.email,
                        subject=f'📋 Loan Application Update - Pending Review',
                        html=html,
                        text=None,
                        log_id=None,
                        is_retry=False,
                    )
                    sent_count += 1
                    logger.info(f"[LOAN APPLICATION TASK] Sent reminder for application #{app.id} to {app.debtor.email}")

            except Exception as e:
                errors.append({
                    'application_id': app.id,
                    'error': str(e)
                })
                logger.error(f"[LOAN APPLICATION TASK] Failed to send reminder for #{app.id}: {e}")

        # Notify admins
        if sent_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title='📧 Pending Application Reminders Sent',
                message=f'Sent {sent_count} reminders for pending applications.',
                type='info',
                metadata={
                    'sent_count': sent_count,
                    'errors': errors[:10]
                },
                user=user
            )

        return {
            'sent_count': sent_count,
            'errors': errors,
            'message': f'Sent {sent_count} reminders'
        }

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Reminder sending failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# BULK IMPORT APPLICATIONS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def bulk_import_applications(self, file_path: str, user: str = 'system', request_data: Optional[dict] = None):
    """
    Bulk import loan applications from CSV file.

    Args:
        file_path: Path to CSV file
        user: User performing the action
        request_data: Additional request data for logging

    Returns:
        dict: {
            'imported': list,
            'errors': list
        }
    """
    logger.info(f"[LOAN APPLICATION TASK] Starting bulk import from {file_path}")

    try:
        from loan_applications.services.loan_application import LoanApplicationService
        result = LoanApplicationService.import_from_csv(
            file_path=file_path,
            user=user,
            request=request_data
        )

        NotificationService.notify_admins_and_staff(
            title='📥 Loan Application Import Completed',
            message=f'Import completed: {len(result.get("imported", []))} imported, {len(result.get("errors", []))} failed.',
            type='info' if not result.get('errors') else 'error',
            metadata=result,
            user=user
        )

        logger.info(f"[LOAN APPLICATION TASK] Bulk import completed: {len(result.get('imported', []))} imported")
        return result

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Bulk import failed: {e}")
        NotificationService.notify_admins_and_staff(
            title='❌ Loan Application Import Failed',
            message=f'Bulk import failed: {str(e)}',
            type='error',
            metadata={'error': str(e)},
            user=user
        )
        raise self.retry(exc=e, countdown=120)


# ============================================================
# FORCE TASKS (Manual Triggers)
# ============================================================

@shared_task
def force_auto_approve(user: str = 'system'):
    """Force immediate auto-approval run."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force auto-approval triggered")
    return auto_approve_applications(user=user)


@shared_task
def force_cleanup_stale(user: str = 'system', days: int = 30):
    """Force immediate stale cleanup."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force stale cleanup triggered")
    return cleanup_stale_applications(days=days, user=user)


@shared_task
def force_pending_reminders(user: str = 'system', days: int = 7):
    """Force immediate pending reminders."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force pending reminders triggered")
    return send_pending_application_reminders(days=days, user=user)