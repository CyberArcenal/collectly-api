import logging
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from debts.models.forgiveness_log import ForgivenessLog
from borrowers.models.borrower import Borrower
from debts.services.debt import DebtService
from notifications.services.notification import NotificationService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class ForgivenessService:
    """
    Service for managing debt forgiveness.
    """

    @staticmethod
    def get_by_id(log_id):
        """
        Get a forgiveness log by ID.
        """
        try:
            return ForgivenessLog.objects.select_related('debt', 'borrower').get(id=log_id)
        except ForgivenessLog.DoesNotExist:
            return None

    @staticmethod
    def get_by_debt(debt_id, page=1, limit=20):
        """
        Get forgiveness logs for a debt.
        """
        qs = ForgivenessLog.objects.filter(
            debt_id=debt_id,
            deleted_at__isnull=True
        ).order_by('-created_at')
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def apply_forgiveness(debt_id, amount_forgiven, reason=None, user=None, request=None):
        """
        Apply debt forgiveness.
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})
        
        if amount_forgiven <= 0:
            raise ValidationError({'amount_forgiven': 'Amount must be positive.'})
        
        if amount_forgiven > debt.total_amount:
            raise ValidationError({'amount_forgiven': 'Cannot forgive more than total amount.'})
        
        # Store old values
        old_total = debt.total_amount
        old_remaining = debt.remaining_amount
        
        # Apply forgiveness
        debt.total_amount -= amount_forgiven
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.save()
        
        # Create forgiveness log
        log_entry = ForgivenessLog.objects.create(
            debt=debt,
            borrower=debt.borrower,
            amount_forgiven=amount_forgiven,
            previous_total_amount=old_total,
            new_total_amount=debt.total_amount,
            reason=reason,
            created_by=user.username if user else 'system',
            status=ForgivenessLog.Status.APPROVED,
            approved_by=user.username if user else None,
            approved_at=timezone.now()
        )
        
        # Create notification for borrower
        if debt.borrower and debt.borrower.email:
            NotificationService.create(
                data={
                    'recipient_email': debt.borrower.email,
                    'title': 'Debt Forgiveness Applied',
                    'message': f"An amount of ₱{amount_forgiven:,.2f} has been forgiven from your debt '{debt.name}'. New balance: ₱{debt.total_amount:,.2f}",
                    'type': 'info',
                },
                user=user,
                request=request
            )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_forgive',
                model_name='Debt',
                object_id=str(debt.id),
                changes={
                    'amount_forgiven': amount_forgiven,
                    'old_total': old_total,
                    'new_total': debt.total_amount,
                    'reason': reason,
                }
            )
        
        logger.info(f"Forgiveness applied to debt #{debt.id}: ₱{amount_forgiven:.2f}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def delete(log_id, user=None, request=None):
        """
        Soft delete a forgiveness log.
        """
        log_entry = ForgivenessService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Forgiveness log not found.'})
        
        log_entry.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='forgiveness_delete',
                model_name='ForgivenessLog',
                object_id=str(log_entry.id),
                changes={'deleted_at': log_entry.deleted_at}
            )
        
        logger.info(f"Forgiveness log deleted: {log_id}")
        return log_entry