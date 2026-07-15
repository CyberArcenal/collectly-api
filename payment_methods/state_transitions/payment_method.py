import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payment_methods.models.payment_method import PaymentMethod
from payment_methods.models.payment_method_stat import PaymentMethodStat
from payments.models.payment_transaction import PaymentTransaction

logger = logging.getLogger(__name__)


class PaymentMethodStateTransitionService:
    """
    Service for handling payment method state transitions.

    Handles creation, update, deletion, and default setting of payment methods.
    Manages stats records and enforces single default.
    """

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def _ensure_single_default(exclude_id=None):
        """
        Ensure only one payment method is set as default.

        Args:
            exclude_id: Payment method ID to exclude from update
        """
        # Find all default methods
        default_methods = PaymentMethod.objects.filter(
            is_default=True,
            deleted_at__isnull=True
        )

        if exclude_id:
            default_methods = default_methods.exclude(id=exclude_id)

        # Unset default for all other methods
        for method in default_methods:
            method.is_default = False
            method.updated_at = timezone.now()
            method.save(update_fields=['is_default', 'updated_at'])

            logger.info(f"[PaymentMethodTransition] Unset default for method #{method.id}")

    @staticmethod
    def _has_transactions(method):
        """
        Check if a payment method has been used in transactions.

        Args:
            method: PaymentMethod instance

        Returns:
            bool: True if method has transactions
        """
        return PaymentTransaction.objects.filter(
            method=method,
            deleted_at__isnull=True
        ).exists()

    @staticmethod
    def _get_next_default():
        """
        Get the next candidate for default payment method.

        Returns:
            PaymentMethod: Next default candidate or None
        """
        return PaymentMethod.objects.filter(
            deleted_at__isnull=True
        ).order_by('id').first()

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_created(method, user="system", request=None):
        """
        Handle post-payment method creation events.

        Args:
            method: PaymentMethod instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentMethod: The created method instance
        """
        logger.info(
            f"[PaymentMethodTransition] on_created: "
            f"method_id={method.id}, name={method.name}, "
            f"is_default={method.is_default}, user={user}"
        )

        # 1. Create stats record (initialized to zero)
        PaymentMethodStat.objects.create(
            method=method,
            transaction_count=0,
            total_amount=0
        )

        # 2. If this method is default, ensure others are not default
        if method.is_default:
            PaymentMethodStateTransitionService._ensure_single_default(method.id)

        # 3. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_method_create',
            model_name='PaymentMethod',
            object_id=str(method.id),
            changes={
                'name': method.name,
                'description': method.description,
                'icon': method.icon,
                'is_default': method.is_default,
            }
        )

        logger.info(f"[PaymentMethodTransition] Payment method #{method.id} created")
        return method

    @staticmethod
    @transaction.atomic
    def on_update(old_method, new_method, user="system", request=None):
        """
        Handle post-payment method update events.

        Args:
            old_method: Old PaymentMethod instance
            new_method: Updated PaymentMethod instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentMethod: The updated method instance
        """
        logger.info(
            f"[PaymentMethodTransition] on_update: "
            f"method_id={new_method.id}, name={new_method.name}, "
            f"is_default={new_method.is_default}, user={user}"
        )
        if not old_method:
            return new_method
        

        # Enforce single default if this method is now default
        if new_method.is_default and not old_method.is_default:
            PaymentMethodStateTransitionService._ensure_single_default(new_method.id)

        # Track changes for audit
        changes = {}
        if old_method.name != new_method.name:
            changes['name'] = {'old': old_method.name, 'new': new_method.name}
        if old_method.description != new_method.description:
            changes['description'] = {'old': old_method.description, 'new': new_method.description}
        if old_method.icon != new_method.icon:
            changes['icon'] = {'old': old_method.icon, 'new': new_method.icon}
        if old_method.is_default != new_method.is_default:
            changes['is_default'] = {'old': old_method.is_default, 'new': new_method.is_default}

        # Audit log if there were changes
        if changes:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_method_update',
                model_name='PaymentMethod',
                object_id=str(new_method.id),
                changes=changes
            )

        logger.info(f"[PaymentMethodTransition] Payment method #{new_method.id} updated")
        return new_method

    @staticmethod
    @transaction.atomic
    def on_set_default(method, user="system", request=None):
        """
        Handle setting a payment method as default.

        Args:
            method: PaymentMethod instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentMethod: The updated method instance
        """
        logger.info(
            f"[PaymentMethodTransition] on_set_default: "
            f"method_id={method.id}, name={method.name}, user={user}"
        )

        # Ensure only one default
        PaymentMethodStateTransitionService._ensure_single_default(method.id)

        # Set this method as default
        method.is_default = True
        method.updated_at = timezone.now()
        method.save(update_fields=['is_default', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_method_set_default',
            model_name='PaymentMethod',
            object_id=str(method.id),
            changes={'is_default': True}
        )

        logger.info(f"[PaymentMethodTransition] Payment method #{method.id} set as default")
        return method

    @staticmethod
    @transaction.atomic
    def on_delete(method, user="system", request=None):
        """
        Handle pre-payment method deletion events.

        Args:
            method: PaymentMethod instance
            user: User performing the action
            request: HTTP request object for audit

        Raises:
            ValidationError: If method has transactions
        """
        logger.info(
            f"[PaymentMethodTransition] on_delete: "
            f"method_id={method.id}, name={method.name}, user={user}"
        )

        # Check if method has been used in transactions
        if PaymentMethodStateTransitionService._has_transactions(method):
            raise ValidationError({
                'detail': f'Cannot delete payment method "{method.name}" because it has been used in transactions.'
            })

        # If this method is default, set another as default
        if method.is_default:
            next_default = PaymentMethodStateTransitionService._get_next_default()
            if next_default and next_default.id != method.id:
                next_default.is_default = True
                next_default.updated_at = timezone.now()
                next_default.save(update_fields=['is_default', 'updated_at'])

                logger.info(
                    f"[PaymentMethodTransition] Set method #{next_default.id} as new default."
                )

        # Soft delete the method
        method.soft_delete()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_method_delete',
            model_name='PaymentMethod',
            object_id=str(method.id),
            changes={
                'name': method.name,
                'deleted_at': method.deleted_at,
            }
        )

        # Stats will be deleted by cascade if foreign key is set with on_delete=CASCADE
        # Otherwise, handle it here
        PaymentMethodStat.objects.filter(method=method).delete()

        logger.info(f"[PaymentMethodTransition] Payment method #{method.id} deleted")