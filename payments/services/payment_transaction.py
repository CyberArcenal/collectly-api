import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.payment_transaction import PaymentTransaction
from debts.models.debt import Debt
from payment_methods.models.payment_method import PaymentMethod
from debts.services.interest_accrual import InterestAccrualService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class PaymentTransactionService:
    """
    Service layer for PaymentTransaction CRUD operations.
    """

    @staticmethod
    def get_by_id(payment_id, include_deleted=False):
        """
        Get a single payment by ID.
        """
        qs = PaymentTransaction.objects.select_related('debt', 'method')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=payment_id)
        except PaymentTransaction.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='payment_date', sort_order='desc'):
        """
        Get paginated list of payments with filters.
        """
        qs = PaymentTransaction.objects.select_related('debt', 'method', 'recorded_by').filter(deleted_at__isnull=True)
        
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
            if filters.get('include_deleted'):
                qs = PaymentTransaction.objects.select_related('debt', 'method')
        
        # Apply sorting
        if sort_order.lower() == 'asc':
            sort_by = sort_by
        else:
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)
        
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new payment transaction.
        """
        debt = Debt.objects.filter(id=data['debt_id']).first()
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})
        
        # Accrue interest first
        InterestAccrualService.apply_accrual(debt, data.get('payment_date'))
        
        # Validate payment amount
        if data['amount'] > debt.remaining_amount:
            raise ValidationError({'amount': f'Payment amount exceeds remaining balance (₱{debt.remaining_amount:,.2f}).'})
        
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
        
        payment = PaymentTransaction.objects.create(
            debt=debt,
            method=method,
            amount=data['amount'],
            payment_date=data['payment_date'],
            reference=reference,
            notes=data.get('notes'),
            recorded_by=data.get('recorded_by'),
            recorded_at=timezone.now()
        )
        
        # Update debt paid_amount and remaining_amount
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
                changes={'data': data}
            )
        
        logger.info(f"Payment created: {payment.id} - ₱{payment.amount:.2f}")
        return payment

    @staticmethod
    @transaction.atomic
    def void_payment(payment_id, user=None, request=None):
        """
        Void a payment (soft delete + reverse amounts).
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
        
        # Update payment method stats
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
    def get_statistics():
        """
        Get payment statistics.
        """
        qs = PaymentTransaction.objects.filter(deleted_at__isnull=True)
        
        total_payments = qs.count()
        total_amount = qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        average_amount = total_amount / total_payments if total_payments > 0 else 0
        
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
            'average_payment_amount': average_amount,
            'payments_last_30_days': recent,
            'by_method': list(by_method),
        }

    @staticmethod
    def _generate_reference():
        """
        Generate unique payment reference.
        """
        import uuid
        from datetime import datetime
        
        date_part = datetime.now().strftime('%Y%m%d')
        random_part = str(uuid.uuid4())[:8].upper()
        return f"PAY-{date_part}-{random_part}"

    @staticmethod
    def get_collection_report(from_date, to_date, target):
        """
        Get collection report for a date range.
        """
        payments = PaymentTransaction.objects.filter(
            deleted_at__isnull=True,
            payment_date__gte=from_date,
            payment_date__lte=to_date
        ).select_related('debt__borrower')
        
        # Group by date
        from collections import defaultdict
        by_date = defaultdict(Decimal)
        by_debtor = defaultdict(lambda: {'total': Decimal('0'), 'count': 0})
        
        for payment in payments:
            date_key = payment.payment_date.isoformat()
            by_date[date_key] += payment.amount
            
            if payment.debt and payment.debt.borrower:
                debtor = payment.debt.borrower
                by_debtor[debtor.id]['total'] += payment.amount
                by_debtor[debtor.id]['count'] += 1
                by_debtor[debtor.id]['name'] = debtor.name
        
        total_actual = sum(by_date.values())
        total_expected = Decimal(str(target))
        collection_rate = (total_actual / total_expected * 100) if total_expected > 0 else 0
        
        # Generate daily data points
        data_points = []
        current = from_date
        while current <= to_date:
            date_key = current.isoformat()
            data_points.append({
                'date': date_key,
                'actual_collected': by_date.get(date_key, Decimal('0')),
                'expected_collected': total_expected / (to_date - from_date + 1).days if target else 0,
            })
            current += timezone.timedelta(days=1)
        
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
            'average_per_day': total_actual / len(data_points) if data_points else 0,
            'data_points': data_points,
            'payments_by_debtor': debtors_list,
        }