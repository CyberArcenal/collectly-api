# payments/tasks/penalty_health_tasks.py
import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from debts.models.debt import Debt
from payments.models.penalty_transaction import PenaltyTransaction
from notifications.services.notification import NotificationService
from system_settings.utils import (
    enable_auto_penalty,
    default_penalty_rate,
    penalty_calculation_method,
    penalty_grace_days,
)
from .penalty_apply_tasks import _has_penalty_since_due_date, PENALTY_APPLICATION_LAST_RUN

logger = logging.getLogger(__name__)


@shared_task
def get_penalty_scheduler_status():
    """Get the status of the penalty scheduler."""
    last_run = cache.get(PENALTY_APPLICATION_LAST_RUN)
    return {
        'enabled': enable_auto_penalty(),
        'last_run': last_run,
        'is_running': True,  # This is a placeholder; could be enhanced with actual task status
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
                'message': 'Debt is overdue but no penalty applied',
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
                'message': 'Debt is fully paid but has penalties',
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