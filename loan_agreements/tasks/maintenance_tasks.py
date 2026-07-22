# loan_agreements/tasks/maintenance_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from loan_agreements.models.loan_agreement import LoanAgreement
from loan_agreements.services.loan_agreement import LoanAgreementService
from debts.models.debt import Debt
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_old_draft_agreements(self, days: int = 30, user: str = 'system'):
    """
    Soft-delete draft agreements that have not been signed for N days.
    """
    logger.info(f"[LOAN AGREEMENT TASK] Cleaning up draft agreements older than {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        draft_agreements = LoanAgreement.objects.filter(
            status=LoanAgreement.Status.DRAFT,
            created_at__lt=cutoff,
            deleted_at__isnull=True
        ).select_related('debt', 'debt__borrower')

        count = draft_agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No old draft agreements found.")
            return {'deleted_count': 0, 'message': 'No old draft agreements found.'}

        deleted_list = []
        for agreement in draft_agreements:
            try:
                LoanAgreementService.delete(
                    agreement_id=agreement.id,
                    user=user,
                    request=None,
                    allow_delete_signed=False
                )
                deleted_list.append(agreement.id)
                logger.info(f"[LOAN AGREEMENT TASK] Deleted draft agreement #{agreement.id}")
            except Exception as e:
                logger.error(f"[LOAN AGREEMENT TASK] Failed to delete agreement #{agreement.id}: {e}")

        if deleted_list:
            NotificationService.notify_admins_and_staff(
                title='📄 Draft Agreement Cleanup Completed',
                message=f'Deleted {len(deleted_list)} draft agreements older than {days} days.',
                type='info',
                metadata={
                    'deleted_count': len(deleted_list),
                    'days': days,
                    'deleted_ids': deleted_list[:10],
                },
                user=user
            )

        return {
            'deleted_count': len(deleted_list),
            'message': f'Deleted {len(deleted_list)} draft agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def sync_agreement_statuses(self, user: str = 'system'):
    """
    Sync agreement statuses with debt statuses (e.g., flag agreements linked to paid debts).
    """
    logger.info("[LOAN AGREEMENT TASK] Syncing agreement statuses with debts...")

    try:
        paid_debts = Debt.objects.filter(
            status=Debt.Status.PAID,
            deleted_at__isnull=True
        ).values_list('id', flat=True)

        agreements = LoanAgreement.objects.filter(
            debt_id__in=paid_debts,
            status=LoanAgreement.Status.SIGNED,
            deleted_at__isnull=True
        )

        count = agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No agreements to sync.")
            return {'updated_count': 0, 'message': 'No agreements to sync.'}

        # For now, only notify (could later add archiving logic)
        NotificationService.notify_admins_and_staff(
            title='📄 Agreement Status Sync Completed',
            message=f'Found {count} agreements linked to paid debts. They might need archiving.',
            type='info',
            metadata={
                'agreement_count': count,
                'paid_debt_ids': list(paid_debts)[:10],
            },
            user=user
        )

        return {
            'updated_count': 0,
            'agreements_linked_to_paid_debts': count,
            'message': f'Found {count} agreements linked to paid debts.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Sync failed: {e}")
        raise self.retry(exc=e, countdown=120)