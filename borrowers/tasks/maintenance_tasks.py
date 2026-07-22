# borrowers/tasks/maintenance_tasks.py
import logging
from datetime import timedelta
from typing import List

from celery import shared_task
from django.db.models import Q, Count
from django.utils import timezone

from borrowers.models.borrower import Borrower
from borrowers.models.credit_check_log import CreditCheckLog
from debts.models.debt import Debt
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def update_borrower_statuses(self, user: str = 'system'):
    """
    Update borrower statuses based on their debts.
    (Currently logs statistics; can be extended with a status field.)
    """
    logger.info("[BORROWER TASK] Starting borrower status updates...")

    try:
        active_borrowers = Borrower.objects.filter(
            deleted_at__isnull=True,
            debts__deleted_at__isnull=True,
            debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).distinct().count()

        inactive_borrowers = Borrower.objects.filter(
            deleted_at__isnull=True
        ).exclude(
            debts__deleted_at__isnull=True,
            debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).distinct().count()

        logger.info(f"[BORROWER TASK] Active borrowers: {active_borrowers}, Inactive: {inactive_borrowers}")

        return {
            'active_borrowers': active_borrowers,
            'inactive_borrowers': inactive_borrowers,
            'message': 'Status check completed'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Status update failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def merge_duplicate_borrowers(self, user: str = 'system'):
    """
    Identify and merge duplicate borrower records based on email/contact.
    """
    logger.info("[BORROWER TASK] Starting duplicate borrower merge...")

    try:
        merged_count = 0
        errors = []

        email_duplicates = Borrower.objects.filter(
            deleted_at__isnull=True,
            email__isnull=False
        ).values('email').annotate(
            count=Count('id')
        ).filter(count__gt=1)

        for item in email_duplicates:
            email = item['email']
            borrowers = list(Borrower.objects.filter(
                email=email,
                deleted_at__isnull=True
            ).order_by('id'))

            if len(borrowers) < 2:
                continue

            primary = borrowers[0]
            for duplicate in borrowers[1:]:
                try:
                    Debt.objects.filter(borrower=duplicate).update(borrower=primary)
                    CreditCheckLog.objects.filter(debtor=duplicate).update(debtor=primary)
                    duplicate.soft_delete()
                    merged_count += 1
                    logger.info(f"[BORROWER TASK] Merged borrower {duplicate.id} into {primary.id} (email: {email})")
                except Exception as e:
                    errors.append({
                        'borrower_id': duplicate.id,
                        'error': str(e)
                    })

        # (Similar logic for contact duplicates could be added here)

        if merged_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title='🔄 Duplicate Borrower Merge Completed',
                message=f'Merged {merged_count} duplicate borrowers, {len(errors)} errors.',
                type='info' if not errors else 'error',
                metadata={
                    'merged_count': merged_count,
                    'errors': errors[:10]
                },
                user=user
            )

        return {
            'merged_count': merged_count,
            'errors': errors,
            'message': f'Merged {merged_count} duplicates'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Duplicate merge failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_incomplete_borrowers(self, days: int = 30, user: str = 'system'):
    """
    Clean up borrowers with incomplete data (missing name, email, contact) older than N days.
    """
    logger.info(f"[BORROWER TASK] Starting incomplete borrower cleanup (older than {days} days)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        incomplete = Borrower.objects.filter(
            deleted_at__isnull=True,
            created_at__lt=cutoff,
        ).filter(
            Q(name__isnull=True) | Q(name='') |
            Q(email__isnull=True) | Q(email='') |
            Q(contact__isnull=True) | Q(contact='')
        )

        count = incomplete.count()
        if count == 0:
            return {'deleted_count': 0, 'message': 'No incomplete borrowers found'}

        for borrower in incomplete:
            borrower.soft_delete()

        logger.info(f"[BORROWER TASK] Soft-deleted {count} incomplete borrowers")

        return {
            'deleted_count': count,
            'message': f'Soft-deleted {count} incomplete borrowers'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)