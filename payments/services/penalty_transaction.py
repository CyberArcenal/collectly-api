import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List

from django.db import transaction
from django.db.models import Q, Sum, Count, Max
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.penalty_transaction import PenaltyTransaction
from debts.models.debt import Debt
from system_settings.utils import (
    default_penalty_rate,
    enable_auto_penalty,
    penalty_calculation_method,
    penalty_grace_days,
)
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class PenaltyTransactionService:
    """
    Service layer for PenaltyTransaction CRUD operations.

    Handles creation, deletion, and retrieval of penalty transactions.
    Also manages auto-penalty generation for overdue debts.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(penalty_id: int, include_deleted: bool = False) -> Optional[PenaltyTransaction]:
        """
        Get a single penalty by ID.

        Args:
            penalty_id: ID of the penalty to retrieve
            include_deleted: Whether to include soft-deleted penalties

        Returns:
            PenaltyTransaction instance or None if not found
        """
        qs = PenaltyTransaction.objects.select_related('debt')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        try:
            return qs.get(id=penalty_id)
        except PenaltyTransaction.DoesNotExist:
            return None

    @staticmethod
    def get_by_debt(debt_id: int, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """
        Get paginated penalties for a specific debt.

        Args:
            debt_id: ID of the debt
            page: Page number for pagination
            limit: Number of items per page

        Returns:
            dict: Paginated list of penalties
        """
        qs = PenaltyTransaction.objects.filter(
            debt_id=debt_id,
            deleted_at__isnull=True
        ).order_by('-penalty_date')

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_list(
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = 'penalty_date',
        sort_order: str = 'desc'
    ) -> Dict[str, Any]:
        """
        Get paginated list of penalties with filters.

        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'

        Returns:
            dict: {
                'data': list of PenaltyTransaction objects,
                'pagination': pagination metadata
            }
        """
        qs = PenaltyTransaction.objects.select_related('debt')

        # Handle deleted filtering based on include_deleted flag
        include_deleted = filters.get('include_deleted', False) if filters else False
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        # Apply filters
        if filters:
            if filters.get('debt_id'):
                qs = qs.filter(debt_id=filters['debt_id'])

            if filters.get('borrower_id'):
                qs = qs.filter(debt__borrower_id=filters['borrower_id'])

            if filters.get('penalty_date_from'):
                qs = qs.filter(penalty_date__gte=filters['penalty_date_from'])

            if filters.get('penalty_date_to'):
                qs = qs.filter(penalty_date__lte=filters['penalty_date_to'])

            if filters.get('min_amount'):
                qs = qs.filter(amount__gte=filters['min_amount'])

            if filters.get('max_amount'):
                qs = qs.filter(amount__lte=filters['max_amount'])

            if filters.get('reason'):
                qs = qs.filter(reason__icontains=filters['reason'])

            if filters.get('is_auto') is not None:
                qs = qs.filter(is_auto=filters['is_auto'])

        # Apply sorting
        if sort_order.lower() == 'asc':
            sort_by = sort_by
        else:
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_statistics() -> Dict[str, Any]:
        """
        Get comprehensive penalty statistics.

        Returns:
            dict: Statistics including totals, averages, and type breakdown
        """
        qs = PenaltyTransaction.objects.filter(deleted_at__isnull=True)

        total_penalties = qs.count()
        total_amount = qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        average_amount = total_amount / total_penalties if total_penalties > 0 else Decimal('0')

        # Auto vs manual
        auto_count = qs.filter(is_auto=True).count()
        manual_count = qs.filter(is_auto=False).count()

        # Last 30 days
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent = qs.filter(penalty_date__gte=thirty_days_ago).count()

        # Top debts by penalty amount
        top_debts = qs.values('debt__id', 'debt__name').annotate(
            total_penalty=Sum('amount'),
            count=Count('id')
        ).order_by('-total_penalty')[:5]

        return {
            'total_penalties': total_penalties,
            'total_penalty_amount': total_amount,
            'average_penalty_amount': round(average_amount, 2),
            'auto_generated': auto_count,
            'manual': manual_count,
            'penalties_last_30_days': recent,
            'top_debts': list(top_debts),
        }

    @staticmethod
    def get_total_penalties_for_debt(debt_id: int) -> Dict[str, Any]:
        """
        Get total penalties and count for a specific debt.

        Args:
            debt_id: ID of the debt

        Returns:
            dict: {
                'total_amount': Decimal,
                'penalty_count': int,
                'last_penalty_date': date or None
            }
        """
        stats = PenaltyTransaction.objects.filter(
            debt_id=debt_id,
            deleted_at__isnull=True
        ).aggregate(
            total_amount=Sum('amount'),
            penalty_count=Count('id'),
            last_penalty_date=Max('penalty_date')
        )

        return {
            'total_amount': stats.get('total_amount') or Decimal('0'),
            'penalty_count': stats.get('penalty_count') or 0,
            'last_penalty_date': stats.get('last_penalty_date'),
        }

    @staticmethod
    def get_penalty_summary_for_borrower(borrower_id: int) -> Dict[str, Any]:
        """
        Get penalty summary for all debts of a borrower.

        Args:
            borrower_id: ID of the borrower

        Returns:
            dict: Summary of penalties across borrower's debts
        """
        penalties = PenaltyTransaction.objects.filter(
            debt__borrower_id=borrower_id,
            deleted_at__isnull=True
        )

        total_penalties = penalties.count()
        total_amount = penalties.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        auto_count = penalties.filter(is_auto=True).count()
        manual_count = penalties.filter(is_auto=False).count()

        return {
            'borrower_id': borrower_id,
            'total_penalties': total_penalties,
            'total_penalty_amount': total_amount,
            'auto_generated': auto_count,
            'manual': manual_count,
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user=None, request=None) -> PenaltyTransaction:
        """
        Create a new penalty transaction.

        This method:
        1. Validates the debt exists and penalty amount is positive
        2. Creates the penalty record
        3. Updates the debt's remaining amount

        Args:
            data: Dictionary containing penalty data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            PenaltyTransaction: The created penalty instance

        Raises:
            ValidationError: If validation fails
        """
        debt = Debt.objects.filter(id=data.get('debt_id')).first()
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})

        # Validate penalty amount
        amount = Decimal(str(data.get('amount', 0)))
        if amount <= 0:
            raise ValidationError({'amount': 'Penalty amount must be positive.'})

        # Validate penalty amount is reasonable (optional guardrail)
        if amount > debt.total_amount * Decimal('0.5'):
            logger.warning(
                f"Large penalty of {amount} applied to debt #{debt.id} "
                f"(total: {debt.total_amount})"
            )

        # Set penalty date if not provided
        penalty_date = data.get('penalty_date')
        if penalty_date is None:
            penalty_date = timezone.now().date()

        # Create penalty
        penalty = PenaltyTransaction.objects.create(
            debt=debt,
            amount=amount,
            penalty_date=penalty_date,
            reason=data.get('reason'),
            is_auto=data.get('is_auto', False)
        )

        # Update debt remaining amount
        debt.remaining_amount += penalty.amount
        debt.save(update_fields=['remaining_amount', 'updated_at'])

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='penalty_create',
                model_name='PenaltyTransaction',
                object_id=str(penalty.id),
                changes={'data': data}
            )

        logger.info(f"Penalty created: {penalty.id} - ₱{penalty.amount:.2f}")
        return penalty

    @staticmethod
    @transaction.atomic
    def delete(penalty_id: int, user=None, request=None) -> PenaltyTransaction:
        """
        Soft delete a penalty.

        This method:
        1. Reverses the penalty amount from the debt
        2. Soft deletes the penalty record

        Args:
            penalty_id: ID of the penalty to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            PenaltyTransaction: The soft-deleted penalty instance

        Raises:
            ValidationError: If penalty not found or already deleted
        """
        penalty = PenaltyTransactionService.get_by_id(penalty_id)
        if not penalty:
            raise ValidationError({'id': 'Penalty not found.'})

        if penalty.deleted_at:
            raise ValidationError({'id': 'Penalty is already deleted.'})

        # Reverse penalty amount from debt
        debt = penalty.debt
        debt.remaining_amount -= penalty.amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.save(update_fields=['remaining_amount', 'updated_at'])

        # Soft delete penalty
        penalty.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='penalty_delete',
                model_name='PenaltyTransaction',
                object_id=str(penalty.id),
                changes={'deleted_at': penalty.deleted_at}
            )

        logger.info(f"Penalty soft-deleted: {penalty.id}")
        return penalty

    @staticmethod
    @transaction.atomic
    def restore(penalty_id: int, user=None, request=None) -> PenaltyTransaction:
        """
        Restore a soft-deleted penalty.

        This method:
        1. Restores the penalty record
        2. Re-applies the penalty amount to the debt

        Args:
            penalty_id: ID of the penalty to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            PenaltyTransaction: The restored penalty instance

        Raises:
            ValidationError: If penalty not found or not deleted
        """
        penalty = PenaltyTransaction.objects.filter(id=penalty_id).first()
        if not penalty:
            raise ValidationError({'id': 'Penalty not found.'})

        if not penalty.deleted_at:
            raise ValidationError({'id': 'Penalty is not deleted.'})

        # Restore penalty
        penalty.restore()

        # Re-apply penalty amount to debt
        debt = penalty.debt
        debt.remaining_amount += penalty.amount
        debt.save(update_fields=['remaining_amount', 'updated_at'])

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='penalty_restore',
                model_name='PenaltyTransaction',
                object_id=str(penalty.id),
                changes={'restored_at': timezone.now()}
            )

        logger.info(f"Penalty restored: {penalty.id}")
        return penalty

    @staticmethod
    @transaction.atomic
    def bulk_create(penalties_data: List[Dict[str, Any]], user=None, request=None) -> Dict[str, Any]:
        """
        Create multiple penalty transactions in bulk.

        Args:
            penalties_data: List of penalty data dictionaries
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            dict: {
                'created': list of created penalties,
                'errors': list of errors
            }
        """
        results = {'created': [], 'errors': []}

        for data in penalties_data:
            try:
                penalty = PenaltyTransactionService.create(
                    data=data,
                    user=user,
                    request=request
                )
                results['created'].append(penalty)
            except Exception as e:
                results['errors'].append({
                    'debt_id': data.get('debt_id'),
                    'error': str(e)
                })

        return results

    # ============================================================
    # AUTO-PENALTY OPERATIONS
    # ============================================================

    @staticmethod
    def run_auto_penalties() -> Dict[str, Any]:
        """
        Run auto-penalty for overdue debts.

        This method:
        1. Finds all overdue debts
        2. Applies penalties based on configuration
        3. Skips debts within grace period
        4. Skips debts that already have a penalty today

        Returns:
            dict: {
                'processed': int,  # Number of penalties applied
                'errors': int,    # Number of errors
                'skipped': int,   # Number of debts skipped
                'message': str    # Status message
            }
        """
        # Check if auto-penalty is enabled
        if not enable_auto_penalty():
            return {
                'processed': 0,
                'errors': 0,
                'skipped': 0,
                'message': 'Auto-penalty disabled in system settings'
            }

        # Get configuration
        grace_days = penalty_grace_days()
        penalty_rate = default_penalty_rate()
        calc_method = penalty_calculation_method()

        today = timezone.now().date()

        # Find overdue debts
        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal('0.01'),
            due_date__lt=today
        )

        processed = 0
        errors = 0
        skipped = 0

        for debt in debts:
            # Check grace period
            days_overdue = (today - debt.due_date).days
            if grace_days > 0 and days_overdue <= grace_days:
                skipped += 1
                continue

            # Check if penalty already applied today
            existing = PenaltyTransaction.objects.filter(
                debt=debt,
                deleted_at__isnull=True,
                penalty_date=today
            ).exists()

            if existing:
                skipped += 1
                continue

            try:
                # Calculate penalty amount
                if calc_method == 'percentage':
                    penalty_amount = debt.remaining_amount * (Decimal(str(penalty_rate)) / Decimal('100'))
                else:  # fixed
                    penalty_amount = Decimal(str(penalty_rate))

                # Round to 2 decimal places
                penalty_amount = round(penalty_amount, 2)

                if penalty_amount <= 0:
                    skipped += 1
                    continue

                # Create penalty
                PenaltyTransactionService.create(
                    data={
                        'debt_id': debt.id,
                        'amount': penalty_amount,
                        'penalty_date': today,
                        'reason': f'Auto-penalty for overdue debt ({days_overdue} days overdue)',
                        'is_auto': True,
                    },
                    user='system'
                )
                processed += 1

                logger.info(
                    f"Auto-penalty applied to debt #{debt.id}: ₱{penalty_amount:.2f} "
                    f"({days_overdue} days overdue)"
                )

            except Exception as e:
                logger.error(f"Error applying auto-penalty to debt #{debt.id}: {e}")
                errors += 1

        logger.info(f"Auto-penalty completed: {processed} processed, {errors} errors, {skipped} skipped")
        return {
            'processed': processed,
            'errors': errors,
            'skipped': skipped,
            'message': f'Auto-penalty completed: {processed} penalties applied, {errors} errors, {skipped} skipped',
        }

    @staticmethod
    def preview_auto_penalties() -> Dict[str, Any]:
        """
        Preview auto-penalties without actually applying them.

        Returns:
            dict: Preview of penalties that would be applied
        """
        if not enable_auto_penalty():
            return {
                'enabled': False,
                'message': 'Auto-penalty disabled in system settings'
            }

        grace_days = penalty_grace_days()
        penalty_rate = default_penalty_rate()
        calc_method = penalty_calculation_method()

        today = timezone.now().date()

        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal('0.01'),
            due_date__lt=today
        )

        preview = []
        total_penalties = Decimal('0')

        for debt in debts:
            days_overdue = (today - debt.due_date).days

            # Check grace period
            if grace_days > 0 and days_overdue <= grace_days:
                preview.append({
                    'debt_id': debt.id,
                    'debt_name': debt.name,
                    'borrower_name': debt.borrower.name,
                    'days_overdue': days_overdue,
                    'penalty_amount': 0,
                    'reason': 'Within grace period',
                    'will_apply': False,
                })
                continue

            # Check if penalty already applied today
            existing = PenaltyTransaction.objects.filter(
                debt=debt,
                deleted_at__isnull=True,
                penalty_date=today
            ).exists()

            if existing:
                preview.append({
                    'debt_id': debt.id,
                    'debt_name': debt.name,
                    'borrower_name': debt.borrower.name,
                    'days_overdue': days_overdue,
                    'penalty_amount': 0,
                    'reason': 'Penalty already applied today',
                    'will_apply': False,
                })
                continue

            # Calculate penalty amount
            if calc_method == 'percentage':
                penalty_amount = debt.remaining_amount * (Decimal(str(penalty_rate)) / Decimal('100'))
            else:
                penalty_amount = Decimal(str(penalty_rate))

            penalty_amount = round(penalty_amount, 2)

            preview.append({
                'debt_id': debt.id,
                'debt_name': debt.name,
                'borrower_name': debt.borrower.name,
                'borrower_id': debt.borrower.id,
                'days_overdue': days_overdue,
                'penalty_amount': penalty_amount,
                'remaining_balance': debt.remaining_amount,
                'will_apply': penalty_amount > 0,
            })

            if penalty_amount > 0:
                total_penalties += penalty_amount

        return {
            'enabled': True,
            'grace_days': grace_days,
            'penalty_rate': penalty_rate,
            'calculation_method': calc_method,
            'as_of_date': today.isoformat(),
            'total_eligible_debts': debts.count(),
            'total_penalties': total_penalties,
            'preview': preview,
        }