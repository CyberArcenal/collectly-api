# loan_agreements/tasks/notification_tasks.py
import logging

from celery import shared_task
from django.utils import timezone

from loan_agreements.models.loan_agreement import LoanAgreement
from debts.models.debt import Debt
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def notify_overdue_agreements(self, user: str = 'system'):
    """
    Find signed agreements where the debt due date has passed, and notify admins/staff.
    """
    logger.info("[LOAN AGREEMENT TASK] Checking for overdue signed agreements...")

    try:
        today = timezone.now().date()

        agreements = LoanAgreement.objects.filter(
            status=LoanAgreement.Status.SIGNED,
            debt__due_date__lt=today,
            debt__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            deleted_at__isnull=True
        ).select_related('debt', 'debt__borrower')

        count = agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No overdue signed agreements.")
            return {'overdue_count': 0, 'message': 'No overdue signed agreements found.'}

        details = []
        for agreement in agreements:
            details.append({
                'agreement_id': agreement.id,
                'debt_id': agreement.debt_id,
                'borrower_name': agreement.debt.borrower.name if agreement.debt.borrower else 'Unknown',
                'due_date': agreement.debt.due_date.isoformat(),
                'days_overdue': (today - agreement.debt.due_date).days,
            })
            logger.info(f"[LOAN AGREEMENT TASK] Overdue agreement #{agreement.id} for debt #{agreement.debt_id}")

        NotificationService.notify_admins_and_staff(
            title='⚠️ Overdue Signed Agreements Detected',
            message=f'Found {count} signed agreements with overdue debts.',
            type='warning',
            metadata={
                'overdue_count': count,
                'details': details[:10],
            },
            user=user
        )

        return {
            'overdue_count': count,
            'details': details[:20],
            'message': f'Found {count} overdue signed agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Notify overdue failed: {e}")
        raise self.retry(exc=e, countdown=120)