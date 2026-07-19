# payment_methods/tasks/payment_method.py
import logging
from datetime import timedelta
from typing import Optional, List

from celery import shared_task
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone

from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from payments.models.payment_transaction import PaymentTransaction
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


# ============================================================
# RECALCULATE PAYMENT METHOD STATS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def recalculate_payment_method_stats(self, method_ids: Optional[List[int]] = None, user: str = 'system'):
    """
    Recalculate stats for payment methods based on actual transactions.

    This ensures stats are accurate even if something went wrong with increment/decrement.

    Args:
        method_ids: Optional list of method IDs to recalc. If None, recalc all.
        user: User performing the action

    Returns:
        dict: {
            'total_recalculated': int,
            'errors': list
        }
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
                # Sum all transactions for this method
                stats = PaymentTransaction.objects.filter(
                    method=method,
                    deleted_at__isnull=True,
                ).aggregate(
                    total_amount=Sum('amount'),
                    total_count=Count('id')
                )

                total_amount = stats['total_amount'] or 0
                total_count = stats['total_count'] or 0

                # Update or create stats
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

        # Log completion
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

        # Notify admins if there were errors
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


# ============================================================
# CLEANUP UNUSED PAYMENT METHODS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_unused_payment_methods(self, days: int = 180, user: str = 'system'):
    """
    Soft-delete unused payment methods (no transactions in N days).

    Args:
        days: Number of days without transactions to consider unused
        user: User performing the action

    Returns:
        dict: {
            'deleted_count': int,
            'skipped_count': int,
            'errors': list
        }
    """
    logger.info(f"[PAYMENT METHOD TASK] Starting unused method cleanup (no transactions for {days} days)...")

    try:
        cutoff_date = timezone.now() - timedelta(days=days)

        # Find methods with no transactions after cutoff
        unused_methods = PaymentMethod.objects.filter(
            deleted_at__isnull=True,
        ).annotate(
            transaction_count=Count('transactions', filter=Q(
                transactions__deleted_at__isnull=True,
                transactions__created_at__gte=cutoff_date
            ))
        ).filter(transaction_count=0)

        # Exclude default method
        unused_methods = unused_methods.exclude(is_default=True)

        deleted_count = 0
        skipped_count = 0
        errors = []

        for method in unused_methods:
            try:
                # Check again if it's used in any transaction (just to be safe)
                has_transaction = PaymentTransaction.objects.filter(
                    method=method,
                    deleted_at__isnull=True
                ).exists()

                if has_transaction:
                    skipped_count += 1
                    continue

                # Soft delete the method
                method.soft_delete()

                # Also soft delete stats
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

        # Notify admins
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


# ============================================================
# FORCE STATS RECALCULATION (manual trigger)
# ============================================================

@shared_task
def force_payment_method_stats_recalc(method_ids: Optional[List[int]] = None, user: str = 'system'):
    """
    Force immediate stats recalculation.
    This is a wrapper for manual triggers from admin panel.
    """
    logger.info("[PAYMENT METHOD TASK] 🔄 Force stats recalculation triggered")
    return recalculate_payment_method_stats(method_ids=method_ids, user=user)


# ============================================================
# GENERATE PAYMENT METHOD USAGE REPORT
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def generate_payment_method_report(self, user: str = 'system'):
    """
    Generate a report of payment method usage statistics.
    For sending to admins periodically.

    Returns:
        dict: Report data
    """
    logger.info("[PAYMENT METHOD TASK] Generating payment method usage report...")

    try:
        from payment_methods.services.payment_method import PaymentMethodService

        # Get summary stats
        summary = PaymentMethodService.get_overall_summary()

        # Get methods with their stats
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

        # Calculate trends (last 30 days vs previous 30 days)
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

        # Notify admins
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


# ============================================================
# AUTO-SYNC DEFAULT METHOD (if default is deleted)
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def ensure_default_payment_method_exists(self, user: str = 'system'):
    """
    Ensure at least one default payment method exists.
    If none exists, set the first available method as default.
    """
    logger.info("[PAYMENT METHOD TASK] Checking for default payment method...")

    try:
        # Check if any default method exists
        default_exists = PaymentMethod.objects.filter(
            is_default=True,
            deleted_at__isnull=True
        ).exists()

        if default_exists:
            logger.info("[PAYMENT METHOD TASK] Default payment method exists, no action needed.")
            return {'action_taken': False, 'message': 'Default already exists'}

        # Find the first available method
        first_method = PaymentMethod.objects.filter(
            deleted_at__isnull=True
        ).order_by('id').first()

        if not first_method:
            logger.warning("[PAYMENT METHOD TASK] No payment methods available to set as default.")
            return {
                'action_taken': False,
                'message': 'No payment methods available'
            }

        # Set as default
        first_method.is_default = True
        first_method.save(update_fields=['is_default', 'updated_at'])

        # Audit log
        log_audit_event(
            request=None,
            user=user,
            action_type='payment_method_auto_default',
            model_name='PaymentMethod',
            object_id=str(first_method.id),
            changes={
                'is_default': True,
                'reason': 'Auto-assigned because no default existed'
            }
        )

        # Notify admins
        NotificationService.notify_admins_and_staff(
            title='🔄 Default Payment Method Auto-Set',
            message=f'Payment method "{first_method.name}" was automatically set as default because no default existed.',
            type='info',
            metadata={'method_id': first_method.id, 'method_name': first_method.name},
            user=user
        )

        logger.info(f"[PAYMENT METHOD TASK] Set {first_method.name} as default (auto).")
        return {
            'action_taken': True,
            'method_id': first_method.id,
            'method_name': first_method.name,
            'message': f'Set {first_method.name} as default'
        }

    except Exception as e:
        logger.error(f"[PAYMENT METHOD TASK] Failed to ensure default: {e}")
        raise self.retry(exc=e, countdown=60)