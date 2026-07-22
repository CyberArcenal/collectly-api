# payment_methods/tasks/cleanup_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q, Count
from django.utils import timezone

from payment_methods.models.payment_method import PaymentMethod
from payments.models.payment_transaction import PaymentTransaction
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_unused_payment_methods(self, days: int = 180, user: str = 'system'):
    """
    Soft-delete unused payment methods (no transactions in N days).
    """
    logger.info(f"[PAYMENT METHOD TASK] Starting unused method cleanup (no transactions for {days} days)...")

    try:
        cutoff_date = timezone.now() - timedelta(days=days)

        unused_methods = PaymentMethod.objects.filter(
            deleted_at__isnull=True,
        ).annotate(
            transaction_count=Count('transactions', filter=Q(
                transactions__deleted_at__isnull=True,
                transactions__created_at__gte=cutoff_date
            ))
        ).filter(transaction_count=0)

        unused_methods = unused_methods.exclude(is_default=True)

        deleted_count = 0
        skipped_count = 0
        errors = []

        for method in unused_methods:
            try:
                has_transaction = PaymentTransaction.objects.filter(
                    method=method,
                    deleted_at__isnull=True
                ).exists()

                if has_transaction:
                    skipped_count += 1
                    continue

                method.soft_delete()
                if hasattr(method, 'stats') and method.stats:
                    method.stats.soft_delete()

                deleted_count += 1
                logger.info(f"[PAYMENT METHOD TASK] Deleted unused method: {method.name}")

            except Exception as e:
                errors.append({
                    'method_id': method.id,
                    'method_name': method.name,
                    'error': str(e)
                })
                logger.error(f"[PAYMENT METHOD TASK] Failed to delete {method.name}: {e}")

        if deleted_count > 0:
            NotificationService.notify_admins_and_staff(
                title='🧹 Unused Payment Methods Cleaned Up',
                message=f'Removed {deleted_count} unused payment methods.',
                type='info',
                metadata={
                    'deleted_count': deleted_count,
                    'skipped_count': skipped_count,
                    'days_threshold': days
                },
                user=user
            )

        result = {
            'deleted_count': deleted_count,
            'skipped_count': skipped_count,
            'errors': errors,
            'days_threshold': days,
            'message': f'Deleted {deleted_count} unused payment methods'
        }

        logger.info(f"[PAYMENT METHOD TASK] Cleanup completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[PAYMENT METHOD TASK] Cleanup failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))