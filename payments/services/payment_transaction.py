import logging
import uuid
from decimal import Decimal
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

from django.db import transaction
from django.db.models import Q, Sum, Count, Max
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.payment_transaction import PaymentTransaction
from debts.models.debt import Debt
from payment_methods.models.payment_method import PaymentMethod
from debts.services.interest_accrual import InterestAccrualService
from system_settings.utils import (
    enable_early_payment_discount,
    early_payment_discount_rate,
)
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class PaymentTransactionService:
    """
    Service layer for PaymentTransaction CRUD operations.

    Handles creation, voiding, and retrieval of payment transactions.
    Also manages interest accrual before payments and payment method statistics.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(payment_id: int, include_deleted: bool = False) -> Optional[PaymentTransaction]:
        """
        Get a single payment by ID.

        Args:
            payment_id: ID of the payment to retrieve
            include_deleted: Whether to include soft-deleted payments

        Returns:
            PaymentTransaction instance or None if not found
        """
        qs = PaymentTransaction.objects.select_related('debt', 'method')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        try:
            return qs.get(id=payment_id)
        except PaymentTransaction.DoesNotExist:
            return None

    @staticmethod
    def get_list(
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = 'payment_date',
        sort_order: str = 'desc'
    ) -> Dict[str, Any]:
        """
        Get paginated list of payments with filters.

        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'

        Returns:
            dict: {
                'data': list of PaymentTransaction objects,
                'pagination': pagination metadata
            }
        """
        qs = PaymentTransaction.objects.select_related('debt', 'method', 'recorded_by')

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

            if filters.get('method_id'):
                qs = qs.filter(method_id=filters['method_id'])

            if filters.get('reference'):
                qs = qs.filter(reference__icontains=filters['reference'])

            if filters.get('payment_date_from'):
                qs = qs.filter(payment_date__gte=filters['payment_date_from'])

            if filters.get('payment_date_to'):
                qs = qs.filter(payment_date__lte=filters['payment_date_to'])

            if filters.get('min_amount'):
                qs = qs.filter(amount__gte=filters['min_amount'])

            if filters.get('max_amount'):
                qs = qs.filter(amount__lte=filters['max_amount'])

            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(reference__icontains=search) |
                    Q(notes__icontains=search) |
                    Q(debt__name__icontains=search) |
                    Q(debt__borrower__name__icontains=search)
                )

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
        Get comprehensive payment statistics.

        Returns:
            dict: Statistics including totals, averages, and method breakdown
        """
        qs = PaymentTransaction.objects.filter(deleted_at__isnull=True)

        total_payments = qs.count()
        total_amount = qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        average_amount = total_amount / total_payments if total_payments > 0 else Decimal('0')

        # Last 30 days
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent = qs.filter(payment_date__gte=thirty_days_ago).count()

        # By method
        by_method = qs.values('method__name').annotate(
            count=Count('id'),
            total=Sum('amount')
        ).order_by('-count')

        return {
            'total_payments': total_payments,
            'total_amount_collected': total_amount,
            'average_payment_amount': round(average_amount, 2),
            'payments_last_30_days': recent,
            'by_method': list(by_method),
        }

    @staticmethod
    def get_collection_report(from_date: date, to_date: date, target: Decimal) -> Dict[str, Any]:
        """
        Get collection report for a date range.

        Args:
            from_date: Start date
            to_date: End date
            target: Expected total collection amount

        Returns:
            dict: Collection report with daily breakdown and debtor summary
        """
        if isinstance(from_date, str):
            from_date = datetime.fromisoformat(from_date).date()
        if isinstance(to_date, str):
            to_date = datetime.fromisoformat(to_date).date()
        if isinstance(target, (int, float)):
            target = Decimal(str(target))

        payments = PaymentTransaction.objects.filter(
            deleted_at__isnull=True,
            payment_date__gte=from_date,
            payment_date__lte=to_date
        ).select_related('debt__borrower')

        # Group by date
        by_date = defaultdict(Decimal)
        by_debtor = defaultdict(lambda: {'total': Decimal('0'), 'count': 0, 'name': ''})

        for payment in payments:
            date_key = payment.payment_date.isoformat()
            by_date[date_key] += payment.amount

            if payment.debt and payment.debt.borrower:
                debtor = payment.debt.borrower
                by_debtor[debtor.id]['total'] += payment.amount
                by_debtor[debtor.id]['count'] += 1
                by_debtor[debtor.id]['name'] = debtor.name

        total_actual = sum(by_date.values())
        total_expected = target
        collection_rate = (total_actual / total_expected * 100) if total_expected > 0 else 0

        # Generate daily data points
        data_points = []
        current = from_date
        days_in_period = (to_date - from_date).days + 1
        daily_expected = total_expected / days_in_period if days_in_period > 0 else Decimal('0')

        while current <= to_date:
            date_key = current.isoformat()
            data_points.append({
                'date': date_key,
                'actual_collected': by_date.get(date_key, Decimal('0')),
                'expected_collected': round(daily_expected, 2),
            })
            current += timedelta(days=1)

        # Sort debtors by total
        debtors_list = []
        for debtor_id, data in by_debtor.items():
            debtors_list.append({
                'debtor_id': debtor_id,
                'debtor_name': data['name'],
                'total_paid': data['total'],
                'transaction_count': data['count'],
            })
        debtors_list.sort(key=lambda x: x['total_paid'], reverse=True)

        return {
            'period': {
                'from': from_date.isoformat(),
                'to': to_date.isoformat(),
            },
            'total_actual': total_actual,
            'total_expected': total_expected,
            'collection_rate': round(collection_rate, 2),
            'average_per_day': round(total_actual / len(data_points) if data_points else 0, 2),
            'data_points': data_points,
            'payments_by_debtor': debtors_list,
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user=None, request=None) -> PaymentTransaction:
        """
        Create a new payment transaction.

        This method:
        1. Accrues interest up to the payment date
        2. Validates payment amount against remaining balance
        3. Applies early payment discount if enabled and applicable
        4. Creates the payment record
        5. Updates debt balances
        6. Updates payment method statistics

        Args:
            data: Dictionary containing payment data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            PaymentTransaction: The created payment instance

        Raises:
            ValidationError: If validation fails
        """
        # Validate debt exists
        debt = Debt.objects.filter(id=data.get('debt_id')).first()
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})

        # Accrue interest up to payment date
        payment_date = data.get('payment_date')
        if isinstance(payment_date, str):
            payment_date = datetime.fromisoformat(payment_date).date()
        elif payment_date is None:
            payment_date = timezone.now().date()

        InterestAccrualService.apply_accrual(debt, payment_date)

        # Get amount and apply early payment discount if applicable
        amount = Decimal(str(data.get('amount')))
        discount_applied = False
        discount_amount = Decimal('0')
        original_amount = amount

        if enable_early_payment_discount() and debt.due_date:
            # Check if payment is early and full
            is_early = payment_date < debt.due_date
            remaining_before_payment = debt.remaining_amount
            is_full_payment = abs(amount - remaining_before_payment) < Decimal('0.01')

            if is_early and is_full_payment:
                discount_rate = early_payment_discount_rate()
                if discount_rate > 0:
                    discount_amount = remaining_before_payment * (Decimal(str(discount_rate)) / Decimal('100'))
                    amount = remaining_before_payment - discount_amount
                    discount_applied = True

        # Validate payment amount does not exceed remaining balance
        if amount > debt.remaining_amount:
            raise ValidationError({
                'amount': f'Payment amount (₱{amount:,.2f}) exceeds remaining balance (₱{debt.remaining_amount:,.2f}).'
            })

        # Validate payment method
        method = None
        if data.get('method_id'):
            method = PaymentMethod.objects.filter(id=data['method_id']).first()
            if not method:
                raise ValidationError({'method_id': 'Payment method not found.'})

        # Generate reference if not provided
        reference = data.get('reference')
        if not reference:
            reference = PaymentTransactionService._generate_reference()

        # Create payment
        payment = PaymentTransaction.objects.create(
            debt=debt,
            method=method,
            amount=amount,
            payment_date=payment_date,
            reference=reference,
            notes=data.get('notes'),
            recorded_by=data.get('recorded_by'),
            recorded_at=timezone.now()
        )

        # Update debt balances
        debt.paid_amount += payment.amount
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.save()

        # Update payment method stats
        if method:
            from payment_methods.services.payment_method import PaymentMethodService
            PaymentMethodService.increment_stats(method.id, payment.amount)

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_create',
                model_name='PaymentTransaction',
                object_id=str(payment.id),
                changes={
                    'data': data,
                    'discount_applied': discount_applied,
                    'discount_amount': float(discount_amount),
                    'original_amount': float(original_amount),
                }
            )

        logger.info(
            f"Payment created: {payment.id} - ₱{payment.amount:.2f} "
            f"{'(discount applied)' if discount_applied else ''}"
        )
        return payment

    @staticmethod
    @transaction.atomic
    def void_payment(payment_id: int, user=None, request=None) -> PaymentTransaction:
        """
        Void a payment (soft delete + reverse amounts).

        Args:
            payment_id: ID of the payment to void
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            PaymentTransaction: The voided payment instance

        Raises:
            ValidationError: If payment not found or already voided
        """
        payment = PaymentTransactionService.get_by_id(payment_id)
        if not payment:
            raise ValidationError({'id': 'Payment not found.'})

        if payment.deleted_at:
            raise ValidationError({'id': 'Payment is already voided.'})

        debt = payment.debt

        # Reverse payment amount
        debt.paid_amount -= payment.amount
        if debt.paid_amount < 0:
            debt.paid_amount = Decimal('0')
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.save()

        # Update payment method stats (decrement)
        if payment.method:
            from payment_methods.services.payment_method import PaymentMethodService
            PaymentMethodService.decrement_stats(payment.method.id, payment.amount)

        # Soft delete payment
        payment.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_void',
                model_name='PaymentTransaction',
                object_id=str(payment.id),
                changes={'voided_at': timezone.now()}
            )

        logger.info(f"Payment voided: {payment.id}")
        return payment

    @staticmethod
    @transaction.atomic
    def update_payment(payment_id: int, data: Dict[str, Any], user=None, request=None, is_admin=False) -> PaymentTransaction:
        """
        Update an existing payment (admin only or within edit window).

        Args:
            payment_id: ID of the payment to update
            data: Dictionary containing updated fields
            user: User performing the action (for audit)
            request: HTTP request object (for audit)
            is_admin: Whether the user is an admin (bypasses time limit)

        Returns:
            PaymentTransaction: The updated payment instance

        Raises:
            ValidationError: If validation fails or payment not found
        """
        payment = PaymentTransactionService.get_by_id(payment_id)
        if not payment:
            raise ValidationError({'id': 'Payment not found.'})

        if payment.deleted_at:
            raise ValidationError({'id': 'Cannot update a voided payment.'})

        # Time limit check (24 hours) unless admin
        if not is_admin:
            hours_since_creation = (timezone.now() - payment.created_at).total_seconds() / 3600
            if hours_since_creation > 24:
                raise ValidationError({
                    'detail': 'Cannot edit payment after 24 hours. Contact admin for assistance.'
                })

        # Validate if amount or debt is changing, need to adjust debt balances
        if data.get('amount') and data['amount'] != payment.amount:
            old_amount = payment.amount
            new_amount = Decimal(str(data['amount']))
            debt = payment.debt

            # Ensure new amount doesn't exceed remaining balance plus old amount
            if new_amount > debt.remaining_amount + old_amount:
                raise ValidationError({
                    'amount': f'New amount (₱{new_amount:,.2f}) exceeds available balance.'
                })

            # Adjust debt balances
            debt.paid_amount -= old_amount
            debt.paid_amount += new_amount
            debt.remaining_amount = debt.total_amount - debt.paid_amount
            if debt.remaining_amount < 0:
                debt.remaining_amount = Decimal('0')
            debt.save()

            # Update payment method stats
            if payment.method:
                from payment_methods.services.payment_method import PaymentMethodService
                PaymentMethodService.decrement_stats(payment.method.id, old_amount)
                PaymentMethodService.increment_stats(payment.method.id, new_amount)

            payment.amount = new_amount

        # Update other fields
        if data.get('payment_date'):
            payment.payment_date = data['payment_date']

        if data.get('reference'):
            payment.reference = data['reference']

        if data.get('notes') is not None:
            payment.notes = data['notes']

        if data.get('method_id'):
            method = PaymentMethod.objects.filter(id=data['method_id']).first()
            if not method:
                raise ValidationError({'method_id': 'Payment method not found.'})
            payment.method = method

        payment.save()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='payment_update',
                model_name='PaymentTransaction',
                object_id=str(payment.id),
                changes={'data': data}
            )

        logger.info(f"Payment updated: {payment.id}")
        return payment

    # ============================================================
    # UTILITY METHODS
    # ============================================================

    @staticmethod
    def _generate_reference() -> str:
        """
        Generate a unique payment reference.

        Returns:
            str: Unique reference in format PAY-YYYYMMDD-XXXXXXXX
        """
        date_part = timezone.now().strftime('%Y%m%d')
        random_part = str(uuid.uuid4())[:8].upper()
        return f"PAY-{date_part}-{random_part}"

    @staticmethod
    def get_total_payments_for_debt(debt_id: int) -> Dict[str, Any]:
        """
        Get total payments and count for a specific debt.

        Args:
            debt_id: ID of the debt

        Returns:
            dict: {
                'total_amount': Decimal,
                'payment_count': int,
                'last_payment_date': date or None
            }
        """
        stats = PaymentTransaction.objects.filter(
            debt_id=debt_id,
            deleted_at__isnull=True
        ).aggregate(
            total_amount=Sum('amount'),
            payment_count=Count('id'),
            last_payment_date=Max('payment_date')
        )

        return {
            'total_amount': stats.get('total_amount') or Decimal('0'),
            'payment_count': stats.get('payment_count') or 0,
            'last_payment_date': stats.get('last_payment_date'),
        }