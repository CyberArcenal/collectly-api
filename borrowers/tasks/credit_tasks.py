# borrowers/tasks/credit_tasks.py
import logging
from typing import Optional, List

from celery import shared_task

from borrowers.models.borrower import Borrower
from borrowers.services.credit_check import CreditCheckService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def recalculate_credit_scores(
    self,
    borrower_ids: Optional[List[int]] = None,
    batch_size: int = 100,
    user: str = 'system'
):
    """
    Recalculate credit scores for all borrowers or a subset.
    """
    logger.info("[BORROWER TASK] Starting credit score recalculation...")

    try:
        qs = Borrower.objects.filter(deleted_at__isnull=True)
        if borrower_ids:
            qs = qs.filter(id__in=borrower_ids)

        total_count = qs.count()
        logger.info(f"[BORROWER TASK] Found {total_count} borrowers to process")

        if total_count == 0:
            return {
                'total': 0,
                'updated': 0,
                'failed': 0,
                'errors': [],
                'message': 'No borrowers to process'
            }

        updated_count = 0
        failed_count = 0
        errors = []

        for start in range(0, total_count, batch_size):
            batch = qs[start:start + batch_size]
            for borrower in batch:
                try:
                    result = CreditCheckService.compute_score(borrower.id)
                    new_score = result.get('score', 700)
                    new_risk_level = result.get('risk_level', 'Medium')
                    remarks = result.get('remarks', '')

                    CreditCheckService.create(
                        data={
                            'debtor_id': borrower.id,
                            'score': new_score,
                            'risk_level': new_risk_level,
                            'remarks': remarks,
                            'performed_by': None,
                        },
                        user=user,
                        request=None
                    )

                    old_rating = borrower.credit_rating
                    new_rating = new_risk_level
                    if old_rating != new_rating:
                        borrower.credit_rating = new_rating
                        borrower.save(update_fields=['credit_rating', 'updated_at'])

                        logger.info(
                            f"[BORROWER TASK] Borrower {borrower.id} credit rating changed: "
                            f"{old_rating} → {new_rating}"
                        )

                    updated_count += 1

                except Exception as e:
                    failed_count += 1
                    errors.append({
                        'borrower_id': borrower.id,
                        'error': str(e)
                    })
                    logger.error(f"[BORROWER TASK] Failed to update borrower {borrower.id}: {e}")

            logger.info(f"[BORROWER TASK] Processed {start + len(batch)}/{total_count}")

        if updated_count > 0 or failed_count > 0:
            NotificationService.notify_admins_and_staff(
                title='🔄 Credit Score Recalculation Completed',
                message=f'Recalculated: {updated_count} updated, {failed_count} failed.',
                type='info' if failed_count == 0 else 'error',
                metadata={
                    'total': total_count,
                    'updated': updated_count,
                    'failed': failed_count,
                    'errors': errors[:10]
                },
                user=user
            )

        result = {
            'total': total_count,
            'updated': updated_count,
            'failed': failed_count,
            'errors': errors,
            'message': f'Updated {updated_count} borrowers, {failed_count} failed'
        }

        logger.info(f"[BORROWER TASK] Score recalculation completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[BORROWER TASK] Credit score recalculation failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_credit_score_recalc(borrower_ids: Optional[List[int]] = None, user: str = 'system'):
    """
    Force immediate credit score recalculation (wrapper for manual triggers).
    """
    logger.info("[BORROWER TASK] 🔄 Force credit score recalculation triggered")
    return recalculate_credit_scores(borrower_ids=borrower_ids, user=user)