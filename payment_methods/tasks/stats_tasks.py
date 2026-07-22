# payment_methods/tasks/stats_tasks.py
import logging
from typing import Optional, List

from celery import shared_task
from django.db.models import Sum, Count, Q
from django.utils import timezone

from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from payments.models.payment_transaction import PaymentTransaction
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def recalculate_payment_method_stats(self, method_ids: Optional[List[int]] = None, user: str = 'system'):
    """
    Recalculate stats for payment methods based on actual transactions.
    """
    logger.info("[PAYMENT METHOD TASK] Starting stats recalculation...")

    try:
        qs = PaymentMethod.objects.filter(deleted_at__isnull=True)
        if method_ids:
            qs = qs.filter(id__in=method_ids)

        total_methods = qs.count()
        updated_count = 0
        errors = []

        for method in qs:
            try:
                stats = PaymentTransaction.objects.filter(
                    method=method,
                    deleted_at__isnull=True,
                ).aggregate(
                    total_amount=Sum('amount'),
                    total_count=Count('id')
                )

                total_amount = stats['total_amount'] or 0
                total_count = stats['total_count'] or 0

                method_stat, created = PaymentMethodStat.objects.get_or_create(
                    method=method,
                    defaults={
                        'transaction_count': total_count,
                        'total_amount': total_amount,
                    }
                )

                if not created:
                    method_stat.transaction_count = total_count
                    method_stat.total_amount = total_amount
                    method_stat.save(update_fields=['transaction_count', 'total_amount', 'updated_at'])

                updated_count += 1
                logger.debug(
                    f"[PAYMENT METHOD TASK] Updated stats for {method.name}: "
                    f"{total_count} transactions, {total_amount} total"
                )

            except Exception as e:
                errors.append({
                    'method_id': method.id,
                    'method_name': method.name,
                    'error': str(e)
                })
                logger.error(f"[PAYMENT METHOD TASK] Failed to recalc stats for {method.name}: {e}")

        log_audit_event(
            request=None,
            user=user,
            action_type='payment_method_stats_recalc',
            model_name='PaymentMethod',
            object_id='stats_recalc',
            changes={
                'total_recalculated': updated_count,
                'errors': len(errors)
            }
        )

        if errors:
            NotificationService.notify_admins_and_staff(
                title='⚠️ Payment Method Stats Recalculation Completed with Errors',
                message=f'Recalculated {updated_count} methods, {len(errors)} errors.',
                type='error',
                metadata={
                    'total_recalculated': updated_count,
                    'errors': errors[:10]
                },
                user=user
            )

        result = {
            'total_recalculated': updated_count,
            'total_methods': total_methods,
            'errors': errors,
            'message': f'Recalculated {updated_count} of {total_methods} payment methods'
        }

        logger.info(f"[PAYMENT METHOD TASK] Stats recalculation completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[PAYMENT METHOD TASK] Stats recalculation failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_payment_method_stats_recalc(method_ids: Optional[List[int]] = None, user: str = 'system'):
    """Force immediate stats recalculation (manual trigger)."""
    logger.info("[PAYMENT METHOD TASK] 🔄 Force stats recalculation triggered")
    return recalculate_payment_method_stats(method_ids=method_ids, user=user)