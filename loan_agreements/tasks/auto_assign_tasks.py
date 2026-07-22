# loan_agreements/tasks/auto_assign_tasks.py
import logging

from celery import shared_task
from django.utils import timezone

from loan_agreements.models.loan_agreement import LoanAgreement
from loan_agreements.services.loan_agreement import LoanAgreementService
from debts.models.debt import Debt
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_assign_agreements(self, user: str = 'system'):
    """
    Auto-create draft agreements for debts that have no agreement yet.
    """
    logger.info("[LOAN AGREEMENT TASK] Auto-assigning agreements to debts without one...")

    try:
        debts_without_agreement = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).exclude(
            id__in=LoanAgreement.objects.filter(
                deleted_at__isnull=True
            ).values_list('debt_id', flat=True).distinct()
        )

        count = debts_without_agreement.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] All debts have agreements.")
            return {'created_count': 0, 'message': 'All debts have agreements.'}

        created = []
        errors = []

        for debt in debts_without_agreement[:50]:  # limit per run
            try:
                data = {
                    'debt_id': debt.id,
                    'status': LoanAgreement.Status.DRAFT,
                    'lender_name': 'Collectly',
                    'agreement_date': timezone.now().date(),
                    'principal_amount': debt.total_amount,
                    'interest_rate': debt.interest_rate,
                    'penalty_rate': debt.penalty_rate,
                    'due_date': debt.due_date,
                }
                agreement = LoanAgreementService.create(data, user=user, request=None)
                created.append(agreement.id)
                logger.info(f"[LOAN AGREEMENT TASK] Created draft agreement #{agreement.id} for debt #{debt.id}")
            except Exception as e:
                errors.append({'debt_id': debt.id, 'error': str(e)})
                logger.error(f"[LOAN AGREEMENT TASK] Failed to create agreement for debt #{debt.id}: {e}")

        if created:
            NotificationService.notify_admins_and_staff(
                title='📄 Auto-Assigned Agreements Created',
                message=f'Created {len(created)} draft agreements for debts without one.',
                type='info',
                metadata={
                    'created_count': len(created),
                    'errors_count': len(errors),
                    'created_ids': created[:10],
                },
                user=user
            )

        return {
            'created_count': len(created),
            'errors': errors,
            'message': f'Created {len(created)} agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Auto-assign failed: {e}")
        raise self.retry(exc=e, countdown=120)