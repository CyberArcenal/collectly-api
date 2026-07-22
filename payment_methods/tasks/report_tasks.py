# payment_methods/tasks/report_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.db.models import Sum

from payment_methods.models.payment_method import PaymentMethod
from payment_methods.services.payment_method import PaymentMethodService
from payments.models.payment_transaction import PaymentTransaction
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def generate_payment_method_report(self, user: str = 'system'):
    """
    Generate a report of payment method usage statistics.
    """
    logger.info("[PAYMENT METHOD TASK] Generating payment method usage report...")

    try:
        summary = PaymentMethodService.get_overall_summary()

        methods = PaymentMethod.objects.filter(deleted_at__isnull=True)
        method_data = []

        for method in methods:
            stats = PaymentMethodService.get_stats(method.id)
            method_data.append({
                'id': method.id,
                'name': method.name,
                'icon': method.icon,
                'is_default': method.is_default,
                'transaction_count': stats.transaction_count,
                'total_amount': float(stats.total_amount),
                'average_transaction': float(stats.average_transaction),
                'percentage_of_total': (
                    (float(stats.total_amount) / summary['total_amount_collected'] * 100)
                    if summary['total_amount_collected'] > 0 else 0
                ),
            })

        thirty_days_ago = timezone.now() - timedelta(days=30)
        sixty_days_ago = timezone.now() - timedelta(days=60)

        trends = {}
        for method in methods:
            recent = PaymentTransaction.objects.filter(
                method=method,
                deleted_at__isnull=True,
                created_at__gte=thirty_days_ago
            ).aggregate(total=Sum('amount'))['total'] or 0

            previous = PaymentTransaction.objects.filter(
                method=method,
                deleted_at__isnull=True,
                created_at__gte=sixty_days_ago,
                created_at__lt=thirty_days_ago
            ).aggregate(total=Sum('amount'))['total'] or 0

            trend_percent = (
                ((float(recent) - float(previous)) / float(previous) * 100)
                if previous > 0 else 100
            )

            trends[str(method.id)] = {
                'recent_amount': float(recent),
                'previous_amount': float(previous),
                'trend_percent': round(trend_percent, 2),
            }

        report = {
            'generated_at': timezone.now().isoformat(),
            'summary': summary,
            'methods': method_data,
            'trends': trends,
        }

        NotificationService.notify_admins_and_staff(
            title='📊 Payment Method Usage Report',
            message=f'Report generated: {summary["total_methods"]} methods, '
                    f'{summary["total_transactions"]} transactions, '
                    f'₱{summary["total_amount_collected"]:,.2f} collected.',
            type='info',
            metadata=report,
            user=user
        )

        logger.info("[PAYMENT METHOD TASK] Report generated successfully")
        return report

    except Exception as e:
        logger.error(f"[PAYMENT METHOD TASK] Report generation failed: {e}")
        raise self.retry(exc=e, countdown=120)