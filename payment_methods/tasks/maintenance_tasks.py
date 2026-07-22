# payment_methods/tasks/maintenance_tasks.py
import logging

from celery import shared_task

from payment_methods.models.payment_method import PaymentMethod
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def ensure_default_payment_method_exists(self, user: str = 'system'):
    """
    Ensure at least one default payment method exists.
    If none exists, set the first available method as default.
    """
    logger.info("[PAYMENT METHOD TASK] Checking for default payment method...")

    try:
        default_exists = PaymentMethod.objects.filter(
            is_default=True,
            deleted_at__isnull=True
        ).exists()

        if default_exists:
            logger.info("[PAYMENT METHOD TASK] Default payment method exists, no action needed.")
            return {'action_taken': False, 'message': 'Default already exists'}

        first_method = PaymentMethod.objects.filter(
            deleted_at__isnull=True
        ).order_by('id').first()

        if not first_method:
            logger.warning("[PAYMENT METHOD TASK] No payment methods available to set as default.")
            return {
                'action_taken': False,
                'message': 'No payment methods available'
            }

        first_method.is_default = True
        first_method.save(update_fields=['is_default', 'updated_at'])

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