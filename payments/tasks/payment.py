import logging
from datetime import datetime, timedelta
from decimal import Decimal
from django.utils import timezone
from celery import shared_task
from django.core.cache import cache
from django.db import transaction

from debts.models.debt import Debt
from payments.models.penalty_transaction import PenaltyTransaction
from payments.services.penalty_transaction import PenaltyTransactionService
from notifications.services.notification import NotificationService
from payments.state_transitions.penalty_transaction import PenaltyTransactionStateTransitionService
from system_settings.utils import (
    enable_auto_penalty,
    default_penalty_rate,
    penalty_calculation_method,
    penalty_grace_days,
)
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)

# Cache key for last run tracking
PENALTY_APPLICATION_LAST_RUN = "penalty_application_last_run"


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def apply_auto_penalties(self):
    """
    Celery task to apply auto-penalties to overdue debts.

    This task runs on a schedule (default: daily at 1:30 AM) and applies penalties
    to debts that are past due beyond the grace period.

    Respects system settings:
    - enableAutoPenalty (master switch)
    - penaltyGraceDays (days before penalty)
    - penaltyCalculationMethod (percentage / fixed)
    - defaultPenaltyRate (rate value)
    - Debt-specific penaltyRate (overrides default)

    Returns:
        dict: {
            'status': str,
            'applied': int,
            'skipped': int,
            'message': str,
            'details': list,
        }
    """
    logger.info("[PENALTY SCHEDULER] Starting auto-penalty application task...")

    try:
        # Check if auto-penalty is enabled
        if not enable_auto_penalty():
            logger.info("[PENALTY SCHEDULER] Auto-penalty is disabled, skipping")
            return {
                'status': 'skipped',
                'message': 'Auto-penalty is disabled in system settings',
                'applied': 0,
                'skipped': 0,
                'details': [],
            }

        # Check if already ran today
        if _penalty_already_ran_today():
            logger.info("[PENALTY SCHEDULER] Already ran today, skipping")
            return {
                'status': 'skipped',
                'message': 'Already ran today',
                'applied': 0,
                'skipped': 0,
                'details': [],
            }

        # Get configuration
        grace_days = penalty_grace_days()
        calc_method = penalty_calculation_method()
        default_rate = default_penalty_rate()

        # Calculate cutoff date (due date must be before this)
        cutoff_date = timezone.now().date() - timedelta(days=grace_days)

        logger.info(
            f"[PENALTY SCHEDULER] Checking for overdue debts with grace period "
            f"({grace_days} days)..."
        )

        # Find debts eligible for penalty
        overdue_debts = Debt.objects.select_related('borrower').filter(
            due_date__lt=cutoff_date,
            remaining_amount__gt=Decimal('0.01'),
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            deleted_at__isnull=True,
        )

        total_count = overdue_debts.count()
        logger.info(f"[PENALTY SCHEDULER] Found {total_count} overdue debts eligible for penalty")

        if total_count == 0:
            logger.info("[PENALTY SCHEDULER] No overdue debts eligible for penalty")
            _mark_penalty_ran_today()
            return {
                'status': 'completed',
                'message': 'No debts eligible for penalty',
                'applied': 0,
                'skipped': 0,
                'details': [],
            }

        applied_count = 0
        skipped_count = 0
        failed_count = 0
        penalty_details = []

        for debt in overdue_debts:
            # Check if penalty already exists for this debt after due date
            if _has_penalty_since_due_date(debt.id, debt.due_date):
                logger.debug(
                    f"[PENALTY SCHEDULER] Debt #{debt.id} already has a penalty "
                    f"since due date, skipping"
                )
                skipped_count += 1
                continue

            # Use debt's own penaltyRate if set, else fallback to default
            penalty_rate = debt.penalty_rate or default_rate

            # Calculate penalty amount
            if calc_method == 'percentage':
                penalty_amount = debt.remaining_amount * (Decimal(str(penalty_rate)) / Decimal('100'))
            else:  # fixed
                penalty_amount = Decimal(str(penalty_rate))

            # Round to 2 decimal places
            penalty_amount = round(penalty_amount, 2)

            if penalty_amount <= 0:
                logger.debug(
                    f"[PENALTY SCHEDULER] Calculated penalty for debt #{debt.id} "
                    f"is zero, skipping"
                )
                skipped_count += 1
                continue

            try:
                # Apply penalty within transaction
                with transaction.atomic():
                    # Create penalty
                    penalty_data = {
                        'debt_id': debt.id,
                        'amount': penalty_amount,
                        'penalty_date': timezone.now().date(),
                        'reason': (
                            f'Auto-penalty for overdue debt '
                            f'({grace_days} days grace, rate {penalty_rate}'
                            f'{calc_method == "percentage" and "%" or " fixed"})'
                        ),
                        'is_auto': True,
                    }

                    penalty = PenaltyTransactionService.create(
                        data=penalty_data,
                        user='system',
                        request=None
                    )

                    # Trigger state transition (updates debt, notifications)
                    transition_service = PenaltyTransactionStateTransitionService()
                    transition_service.on_collect(penalty, user='system', request=None)

                    applied_count += 1
                    penalty_details.append({
                        'debt_id': debt.id,
                        'debt_name': debt.name,
                        'borrower_name': debt.borrower.name if debt.borrower else 'Unknown',
                        'penalty_amount': float(penalty_amount),
                        'penalty_id': penalty.id,
                        'days_overdue': (timezone.now().date() - debt.due_date).days,
                    })

                    logger.info(
                        f"[PENALTY SCHEDULER] Applied penalty of ₱{penalty_amount:.2f} "
                        f"to debt #{debt.id}"
                    )

            except Exception as e:
                failed_count += 1
                logger.error(
                    f"[PENALTY SCHEDULER] Failed to apply penalty for debt #{debt.id}: {e}"
                )

        # Log summary
        log_audit_event(
            request=None,
            user='system',
            action_type='export_data',
            model_name='PenaltyApplicationScheduler',
            object_id='auto_penalty',
            changes={
                'applied': applied_count,
                'skipped': skipped_count,
                'failed': failed_count,
                'date': timezone.now().isoformat(),
                'grace_days': grace_days,
                'calculation_method': calc_method,
                'default_rate': default_rate,
            }
        )

        # Send notification to admins/staff
        try:
            if applied_count > 0 or failed_count > 0:
                NotificationService.notify_admins_and_staff(
                    title='💰 Auto-Penalty Application Completed',
                    message=(
                        f'Auto-penalties applied: {applied_count} applied, '
                        f'{skipped_count} skipped, {failed_count} failed.'
                    ),
                    type='info' if failed_count == 0 else 'error',
                    metadata={
                        'applied': applied_count,
                        'skipped': skipped_count,
                        'failed': failed_count,
                        'total_checked': total_count,
                        'grace_days': grace_days,
                        'calculation_method': calc_method,
                    },
                    user='system'
                )
        except Exception as e:
            logger.warning(f"[PENALTY SCHEDULER] Could not send notification: {e}")

        # Mark as ran today
        _mark_penalty_ran_today()

        logger.info(
            f"[PENALTY SCHEDULER] Completed: {applied_count} applied, "
            f"{skipped_count} skipped, {failed_count} failed"
        )

        return {
            'status': 'completed' if failed_count == 0 else 'completed_with_failures',
            'applied': applied_count,
            'skipped': skipped_count,
            'failed': failed_count,
            'total_checked': total_count,
            'message': f'Applied {applied_count} penalties ({failed_count} failed)',
            'details': penalty_details,
        }

    except Exception as e:
        logger.error(f"[PENALTY SCHEDULER] ❌ Error during penalty application: {e}")

        try:
            NotificationService.notify_admins_and_staff(
                title='❌ Auto-Penalty Application Failed',
                message=f'Failed to apply auto-penalties: {str(e)}',
                type='error',
                metadata={'error': str(e)},
                user='system'
            )
        except Exception as notif_err:
            logger.warning(f"[PENALTY SCHEDULER] Could not send failure notification: {notif_err}")

        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_penalty_application():
    """
    Force immediate penalty application run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[PENALTY SCHEDULER] 🔄 Force penalty application triggered")
    return apply_auto_penalties()


@shared_task
def apply_penalty_to_specific_debt(debt_id):
    """
    Manually apply a penalty to a specific debt.

    Args:
        debt_id: ID of the debt

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'message': str,
            'penalty_amount': float,
            'penalty_id': int,
        }
    """
    try:
        debt = Debt.objects.select_related('borrower').filter(
            id=debt_id,
            deleted_at__isnull=True
        ).first()

        if not debt:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Debt not found',
            }

        if debt.status not in [Debt.Status.ACTIVE, Debt.Status.OVERDUE]:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': f'Debt is not active or overdue (status: {debt.status})',
            }

        if debt.remaining_amount <= Decimal('0.01'):
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Debt is fully paid',
            }

        today = timezone.now().date()
        grace_days = penalty_grace_days()
        cutoff_date = today - timedelta(days=grace_days)

        if debt.due_date >= cutoff_date:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': f'Debt is within grace period (due date: {debt.due_date})',
            }

        # Check if penalty already exists
        if _has_penalty_since_due_date(debt.id, debt.due_date):
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Penalty already exists for this debt since due date',
            }

        # Calculate penalty
        calc_method = penalty_calculation_method()
        penalty_rate = debt.penalty_rate or default_penalty_rate()

        if calc_method == 'percentage':
            penalty_amount = debt.remaining_amount * (Decimal(str(penalty_rate)) / Decimal('100'))
        else:
            penalty_amount = Decimal(str(penalty_rate))

        penalty_amount = round(penalty_amount, 2)

        if penalty_amount <= 0:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Calculated penalty amount is zero',
            }

        # Apply penalty
        with transaction.atomic():
            penalty_data = {
                'debt_id': debt.id,
                'amount': penalty_amount,
                'penalty_date': today,
                'reason': f'Manual penalty applied by system',
                'is_auto': True,
            }

            penalty = PenaltyTransactionService.create(
                data=penalty_data,
                user='system',
                request=None
            )

            transition_service = PenaltyTransactionStateTransitionService()
            transition_service.on_collect(penalty, user='system', request=None)

        log_audit_event(
            request=None,
            user='system',
            action_type='penalty_manual_apply',
            model_name='PenaltyTransaction',
            object_id=str(penalty.id),
            changes={
                'debt_id': debt_id,
                'amount': float(penalty_amount),
                'reason': 'Manual application',
            }
        )

        return {
            'debt_id': debt_id,
            'success': True,
            'penalty_amount': float(penalty_amount),
            'penalty_id': penalty.id,
            'message': f'Penalty of ₱{penalty_amount:.2f} applied to debt #{debt_id}',
        }

    except Exception as e:
        logger.error(f"[PENALTY SCHEDULER] Error applying penalty to debt #{debt_id}: {e}")
        return {
            'debt_id': debt_id,
            'success': False,
            'message': str(e),
        }


@shared_task
def preview_penalty_application():
    """
    Preview which debts would receive penalties without actually applying them.

    Returns:
        dict: {
            'count': int,
            'debts': list,
            'as_of_date': str,
            'settings': dict,
        }
    """
    today = timezone.now().date()
    grace_days = penalty_grace_days()
    calc_method = penalty_calculation_method()
    default_rate = default_penalty_rate()

    cutoff_date = today - timedelta(days=grace_days)

    debts = Debt.objects.select_related('borrower').filter(
        due_date__lt=cutoff_date,
        remaining_amount__gt=Decimal('0.01'),
        status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
        deleted_at__isnull=True,
    )

    preview_data = []
    for debt in debts:
        # Check if already penalized
        already_penalized = _has_penalty_since_due_date(debt.id, debt.due_date)

        penalty_rate = debt.penalty_rate or default_rate

        if calc_method == 'percentage':
            penalty_amount = debt.remaining_amount * (Decimal(str(penalty_rate)) / Decimal('100'))
        else:
            penalty_amount = Decimal(str(penalty_rate))

        penalty_amount = round(penalty_amount, 2)

        preview_data.append({
            'debt_id': debt.id,
            'debt_name': debt.name,
            'borrower_name': debt.borrower.name if debt.borrower else 'Unknown',
            'due_date': debt.due_date.isoformat(),
            'days_overdue': (today - debt.due_date).days,
            'remaining_balance': float(debt.remaining_amount),
            'penalty_rate': float(penalty_rate),
            'penalty_amount': float(penalty_amount),
            'already_penalized': already_penalized,
            'will_apply': penalty_amount > 0 and not already_penalized,
        })

    return {
        'count': len(preview_data),
        'debts': preview_data,
        'as_of_date': today.isoformat(),
        'settings': {
            'grace_days': grace_days,
            'calculation_method': calc_method,
            'default_rate': default_rate,
        },
    }


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _penalty_already_ran_today():
    """
    Check if the penalty task already ran today.

    Returns:
        bool: True if already ran today
    """
    last_run = cache.get(PENALTY_APPLICATION_LAST_RUN)
    if not last_run:
        return False

    last_run_date = last_run.get('date')
    if not last_run_date:
        return False

    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        today = timezone.now().date()
        return last_run_date == today
    except (ValueError, TypeError):
        return False


def _mark_penalty_ran_today():
    """
    Mark today as the last run date.
    """
    cache.set(
        PENALTY_APPLICATION_LAST_RUN,
        {
            'date': timezone.now().isoformat(),
            'timestamp': timezone.now().isoformat(),
        },
        timeout=86400 * 2  # 2 days
    )


def _has_penalty_since_due_date(debt_id, due_date):
    """
    Check if a penalty already exists for this debt after its due date.

    Args:
        debt_id: Debt ID
        due_date: Due date to check from

    Returns:
        bool: True if penalty exists
    """
    return PenaltyTransaction.objects.filter(
        debt_id=debt_id,
        penalty_date__gte=due_date,
        deleted_at__isnull=True,
    ).exists()


@shared_task
def get_penalty_scheduler_status():
    """
    Get the status of the penalty scheduler.

    Returns:
        dict: {
            'enabled': bool,
            'last_run': dict or None,
            'is_running': bool,
            'schedule': str,
            'settings': dict,
        }
    """
    last_run = cache.get(PENALTY_APPLICATION_LAST_RUN)

    return {
        'enabled': enable_auto_penalty(),
        'last_run': last_run,
        'is_running': True,
        'schedule': 'Daily at 1:30 AM',
        'settings': {
            'grace_days': penalty_grace_days(),
            'calculation_method': penalty_calculation_method(),
            'default_rate': default_penalty_rate(),
        },
    }


@shared_task
def check_penalty_application_health():
    """
    Health check task to verify penalty application status.

    Checks for:
    - Debts that should have penalties but don't
    - Debts with penalties but are fully paid

    Returns:
        dict: {
            'issues_found': int,
            'issues': list,
        }
    """
    today = timezone.now().date()
    grace_days = penalty_grace_days()
    cutoff_date = today - timedelta(days=grace_days)
    issues = []

    # Check 1: Debts that should have penalties but don't
    debts_without_penalty = Debt.objects.select_related('borrower').filter(
        due_date__lt=cutoff_date,
        remaining_amount__gt=Decimal('0.01'),
        status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
        deleted_at__isnull=True,
    )

    for debt in debts_without_penalty:
        if not _has_penalty_since_due_date(debt.id, debt.due_date):
            issues.append({
                'type': 'missing_penalty',
                'debt_id': debt.id,
                'debt_name': debt.name,
                'borrower_name': debt.borrower.name if debt.borrower else 'Unknown',
                'due_date': debt.due_date.isoformat(),
                'days_overdue': (today - debt.due_date).days,
                'remaining_balance': float(debt.remaining_amount),
                'message': f'Debt is overdue but no penalty applied',
            })

    # Check 2: Debts with penalties but fully paid
    paid_with_penalties = PenaltyTransaction.objects.filter(
        deleted_at__isnull=True,
    ).values_list('debt_id', flat=True).distinct()

    for debt_id in paid_with_penalties:
        debt = Debt.objects.filter(id=debt_id, deleted_at__isnull=True).first()
        if debt and debt.remaining_amount <= Decimal('0.01'):
            issues.append({
                'type': 'paid_with_penalty',
                'debt_id': debt.id,
                'debt_name': debt.name,
                'remaining_balance': float(debt.remaining_amount),
                'message': f'Debt is fully paid but has penalties',
            })

    # Send alert if issues found
    if issues:
        try:
            NotificationService.notify_admins_and_staff(
                title='⚠️ Penalty Application Health Check Issues Found',
                message=f'Found {len(issues)} issues in penalty applications.',
                type='error',
                metadata={
                    'issues_found': len(issues),
                    'missing_penalty_count': sum(1 for i in issues if i['type'] == 'missing_penalty'),
                    'paid_with_penalty_count': sum(1 for i in issues if i['type'] == 'paid_with_penalty'),
                },
                user='system'
            )
        except Exception as e:
            logger.warning(f"[PENALTY SCHEDULER] Could not send health check notification: {e}")

    return {
        'issues_found': len(issues),
        'missing_penalty_count': sum(1 for i in issues if i['type'] == 'missing_penalty'),
        'paid_with_penalty_count': sum(1 for i in issues if i['type'] == 'paid_with_penalty'),
        'issues': issues[:20],
    }