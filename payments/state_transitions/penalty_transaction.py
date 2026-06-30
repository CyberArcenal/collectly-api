import logging
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.penalty_transaction import PenaltyTransaction
from debts.models.debt import Debt
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


class PenaltyTransactionStateTransitionService:
    """
    Service for handling penalty transaction state transitions.

    Handles penalty collection, waiver, and reversal.
    Manages debt balance updates, notifications, and audit logging.
    """

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    def _send_in_app_notification(title, message, metadata=None, user="system"):
        """
        Send in-app notification.

        Args:
            title: Notification title
            message: Notification message
            metadata: Additional metadata
            user: User performing the action
        """
        try:
            NotificationService.create(
                data={
                    'title': title,
                    'message': message,
                    'type': 'info',
                    'metadata': metadata or {},
                },
                user=user,
                request=None
            )
            return True
        except Exception as e:
            logger.error(f"[Notification] Failed to create notification: {e}")
            return False

    @staticmethod
    def _get_debt_with_borrower(debt_id):
        """
        Get debt with borrower relation.

        Args:
            debt_id: Debt ID

        Returns:
            Debt: Debt instance with borrower loaded

        Raises:
            ValidationError: If debt not found
        """
        debt = Debt.objects.select_related('borrower').filter(
            id=debt_id,
            deleted_at__isnull=True
        ).first()

        if not debt:
            raise ValidationError({'detail': f'Debt #{debt_id} not found'})

        return debt

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_collect(penalty, user="system", request=None):
        """
        Handle penalty collection (apply penalty to debt).

        Args:
            penalty: PenaltyTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PenaltyTransaction: The collected penalty instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PenaltyTransition] on_collect: penalty_id={penalty.id}, "
            f"amount={penalty.amount}, debt_id={penalty.debt_id}, user={user}"
        )

        if penalty.deleted_at:
            raise ValidationError({'detail': 'Penalty is already deleted.'})

        if penalty.status == 'collected':
            logger.info(f"[PenaltyTransition] Penalty #{penalty.id} already collected")
            return penalty

        # Get debt with borrower
        debt_with_borrower = PenaltyTransactionStateTransitionService._get_debt_with_borrower(
            penalty.debt_id
        )

        # Store old values for audit
        old_remaining = debt_with_borrower.remaining_amount

        # Apply penalty to debt
        debt_with_borrower.remaining_amount += penalty.amount
        debt_with_borrower.updated_at = timezone.now()
        debt_with_borrower.save(update_fields=['remaining_amount', 'updated_at'])

        # Mark penalty as collected
        old_status = penalty.status
        penalty.status = 'collected'
        penalty.updated_at = timezone.now()
        penalty.save(update_fields=['status', 'updated_at'])

        # In-app notification to debtor
        borrower = debt_with_borrower.borrower
        if borrower:
            PenaltyTransactionStateTransitionService._send_in_app_notification(
                title="⚠️ Penalty Applied",
                message=(
                    f'A penalty of ₱{penalty.amount:,.2f} has been added to your debt '
                    f'"{debt_with_borrower.name}". New balance: ₱{debt_with_borrower.remaining_amount:,.2f}.'
                ),
                metadata={
                    'penalty_id': penalty.id,
                    'debt_id': debt_with_borrower.id,
                    'borrower_id': borrower.id,
                    'amount': float(penalty.amount),
                    'new_balance': float(debt_with_borrower.remaining_amount),
                },
                user=user,
            )

        # Audit logs
        log_audit_event(
            request=request,
            user=user,
            action_type='penalty_collect',
            model_name='PenaltyTransaction',
            object_id=str(penalty.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'collected'},
            }
        )

        log_audit_event(
            request=request,
            user=user,
            action_type='debt_penalty',
            model_name='Debt',
            object_id=str(debt_with_borrower.id),
            changes={
                'before': {'remaining_amount': float(old_remaining)},
                'after': {'remaining_amount': float(debt_with_borrower.remaining_amount)},
                'penalty_id': penalty.id,
                'penalty_amount': float(penalty.amount),
            }
        )

        logger.info(
            f"[PenaltyTransition] Penalty #{penalty.id} collected. "
            f"Debt #{debt_with_borrower.id} remaining={debt_with_borrower.remaining_amount}"
        )

        return penalty

    @staticmethod
    @transaction.atomic
    def on_waive(penalty, reason="", user="system", request=None):
        """
        Handle penalty waiver.

        Args:
            penalty: PenaltyTransaction instance
            reason: Reason for waiver
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PenaltyTransaction: The waived penalty instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PenaltyTransition] on_waive: penalty_id={penalty.id}, "
            f"user={user}, reason={reason}"
        )

        if penalty.deleted_at:
            raise ValidationError({'detail': 'Penalty is already deleted.'})

        if penalty.status == 'waived':
            logger.info(f"[PenaltyTransition] Penalty #{penalty.id} already waived")
            return penalty

        # If penalty was collected, reverse its effect on debt
        if penalty.status == 'collected':
            debt_with_borrower = PenaltyTransactionStateTransitionService._get_debt_with_borrower(
                penalty.debt_id
            )

            old_remaining = debt_with_borrower.remaining_amount

            debt_with_borrower.remaining_amount -= penalty.amount
            if debt_with_borrower.remaining_amount < 0:
                debt_with_borrower.remaining_amount = Decimal('0')
            debt_with_borrower.updated_at = timezone.now()
            debt_with_borrower.save(update_fields=['remaining_amount', 'updated_at'])

            # Audit log for debt adjustment
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_penalty_waive',
                model_name='Debt',
                object_id=str(debt_with_borrower.id),
                changes={
                    'before': {'remaining_amount': float(old_remaining)},
                    'after': {'remaining_amount': float(debt_with_borrower.remaining_amount)},
                    'penalty_id': penalty.id,
                    'penalty_amount': float(penalty.amount),
                }
            )

        # Mark penalty as waived
        old_status = penalty.status
        penalty.status = 'waived'
        penalty.updated_at = timezone.now()
        penalty.save(update_fields=['status', 'updated_at'])

        # In-app notification to debtor
        debt_with_borrower = PenaltyTransactionStateTransitionService._get_debt_with_borrower(
            penalty.debt_id
        )

        borrower = debt_with_borrower.borrower
        if borrower:
            PenaltyTransactionStateTransitionService._send_in_app_notification(
                title="✅ Penalty Waived",
                message=(
                    f'The penalty of ₱{penalty.amount:,.2f} on debt '
                    f'"{debt_with_borrower.name}" has been waived. '
                    f'Reason: {reason or "N/A"}.'
                ),
                metadata={
                    'penalty_id': penalty.id,
                    'debt_id': debt_with_borrower.id,
                    'borrower_id': borrower.id,
                    'amount': float(penalty.amount),
                    'reason': reason,
                },
                user=user,
            )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='penalty_waive',
            model_name='PenaltyTransaction',
            object_id=str(penalty.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'waived'},
                'reason': reason,
            }
        )

        logger.info(f"[PenaltyTransition] Penalty #{penalty.id} waived")
        return penalty

    @staticmethod
    @transaction.atomic
    def on_reverse(penalty, user="system", request=None):
        """
        Handle penalty reversal.

        Args:
            penalty: PenaltyTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PenaltyTransaction: The reversed penalty instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PenaltyTransition] on_reverse: penalty_id={penalty.id}, "
            f"user={user}"
        )

        if penalty.deleted_at:
            raise ValidationError({'detail': 'Penalty is already deleted.'})

        if penalty.status == 'reversed':
            logger.info(f"[PenaltyTransition] Penalty #{penalty.id} already reversed")
            return penalty

        # Get debt with borrower
        debt_with_borrower = PenaltyTransactionStateTransitionService._get_debt_with_borrower(
            penalty.debt_id
        )

        old_status = penalty.status
        old_remaining = debt_with_borrower.remaining_amount

        # If penalty was collected, reverse its effect on debt
        if old_status == 'collected':
            debt_with_borrower.remaining_amount -= penalty.amount
            if debt_with_borrower.remaining_amount < 0:
                debt_with_borrower.remaining_amount = Decimal('0')
            debt_with_borrower.updated_at = timezone.now()
            debt_with_borrower.save(update_fields=['remaining_amount', 'updated_at'])

            # Audit log for debt adjustment
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_penalty_reverse',
                model_name='Debt',
                object_id=str(debt_with_borrower.id),
                changes={
                    'before': {'remaining_amount': float(old_remaining)},
                    'after': {'remaining_amount': float(debt_with_borrower.remaining_amount)},
                    'penalty_id': penalty.id,
                    'penalty_amount': float(penalty.amount),
                }
            )

        # Mark penalty as reversed
        penalty.status = 'reversed'
        penalty.updated_at = timezone.now()
        penalty.save(update_fields=['status', 'updated_at'])

        # In-app notification to debtor
        borrower = debt_with_borrower.borrower
        if borrower:
            PenaltyTransactionStateTransitionService._send_in_app_notification(
                title="🔄 Penalty Reversed",
                message=(
                    f'The penalty of ₱{penalty.amount:,.2f} on debt '
                    f'"{debt_with_borrower.name}" has been reversed.'
                ),
                metadata={
                    'penalty_id': penalty.id,
                    'debt_id': debt_with_borrower.id,
                    'borrower_id': borrower.id,
                    'amount': float(penalty.amount),
                },
                user=user,
            )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='penalty_reverse',
            model_name='PenaltyTransaction',
            object_id=str(penalty.id),
            changes={
                'before': {'status': old_status},
                'after': {'status': 'reversed'},
            }
        )

        logger.info(f"[PenaltyTransition] Penalty #{penalty.id} reversed")
        return penalty