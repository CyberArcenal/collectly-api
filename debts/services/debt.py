import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from borrowers.models.borrower import Borrower
from utils.pagination import paginate_queryset
from system_settings.services.setting import SystemSettingService

logger = logging.getLogger(__name__)


class DebtService:
    """
    Service layer for Debt CRUD operations.
    """

    @staticmethod
    def get_by_id(debt_id, include_deleted=False):
        """
        Get a single debt by ID.
        """
        qs = Debt.objects.select_related('borrower')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=debt_id)
        except Debt.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='due_date', sort_order='asc'):
        """
        Get paginated list of debts with filters.
        """
        qs = Debt.objects.select_related('borrower').filter(deleted_at__isnull=True)
        
        # Apply filters
        if filters:
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(name__icontains=search) |
                    Q(borrower__name__icontains=search) |
                    Q(borrower__email__icontains=search)
                )
            if filters.get('status'):
                qs = qs.filter(status=filters['status'])
            if filters.get('borrower_id'):
                qs = qs.filter(borrower_id=filters['borrower_id'])
            if filters.get('due_date_from'):
                qs = qs.filter(due_date__gte=filters['due_date_from'])
            if filters.get('due_date_to'):
                qs = qs.filter(due_date__lte=filters['due_date_to'])
            if filters.get('min_total_amount'):
                qs = qs.filter(total_amount__gte=filters['min_total_amount'])
            if filters.get('max_total_amount'):
                qs = qs.filter(total_amount__lte=filters['max_total_amount'])
            if filters.get('include_deleted'):
                qs = Debt.objects.select_related('borrower')
        
        # Apply sorting
        if sort_order.lower() == 'desc':
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)
        
        # Paginate
        result = paginate_queryset(qs, page, limit)
        
        # Add stats to each debt
        for debt in result['data']:
            debt.stats = DebtService._get_debt_stats(debt)
        
        return result

    @staticmethod
    def _get_debt_stats(debt):
        """
        Calculate stats for a single debt.
        """
        from payments.models.payment_transaction import PaymentTransaction
        from payments.models.penalty_transaction import PenaltyTransaction
        
        total_paid = PaymentTransaction.objects.filter(
            debt_id=debt.id,
            deleted_at__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        total_penalty = PenaltyTransaction.objects.filter(
            debt_id=debt.id,
            deleted_at__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        payment_count = PaymentTransaction.objects.filter(
            debt_id=debt.id,
            deleted_at__isnull=True
        ).count()
        
        penalty_count = PenaltyTransaction.objects.filter(
            debt_id=debt.id,
            deleted_at__isnull=True
        ).count()
        
        last_payment = PaymentTransaction.objects.filter(
            debt_id=debt.id,
            deleted_at__isnull=True
        ).order_by('-payment_date').first()
        
        remaining_balance = debt.total_amount - total_paid
        
        # Calculate days overdue
        days_overdue = 0
        if debt.due_date and remaining_balance > 0:
            today = timezone.now().date()
            if debt.due_date < today:
                days_overdue = (today - debt.due_date).days
        
        is_fully_paid = remaining_balance <= Decimal('0.01')
        
        return {
            'total_paid': total_paid,
            'total_penalty': total_penalty,
            'remaining_balance': remaining_balance,
            'days_overdue': days_overdue,
            'payment_count': payment_count,
            'penalty_count': penalty_count,
            'last_payment_date': last_payment.payment_date if last_payment else None,
            'is_fully_paid': is_fully_paid,
        }

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new debt.
        """
        borrower = Borrower.objects.filter(id=data['borrower_id']).first()
        if not borrower:
            raise ValidationError({'borrower_id': 'Borrower not found.'})
        
        # Get default rates if not provided
        interest_rate = data.get('interest_rate')
        if interest_rate is None:
            interest_rate = SystemSettingService.get_value('default_interest_rate', 'collections', 10)
        
        penalty_rate = data.get('penalty_rate')
        if penalty_rate is None:
            penalty_rate = SystemSettingService.get_value('default_penalty_rate', 'collections', 2)
        
        debt = Debt.objects.create(
            borrower=borrower,
            name=data['name'],
            total_amount=data['total_amount'],
            paid_amount=data.get('paid_amount', Decimal('0')),
            due_date=data['due_date'],
            status=data.get('status', Debt.Status.ACTIVE),
            interest_rate=interest_rate,
            penalty_rate=penalty_rate,
            interest_calculation_period=data.get(
                'interest_calculation_period',
                Debt.InterestPeriod.PER_ANNUM
            ),
            last_interest_accrual_date=data.get('last_interest_accrual_date')
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_create',
                model_name='Debt',
                object_id=str(debt.id),
                changes={'data': data}
            )
        
        logger.info(f"Debt created: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def update(debt_id, data, user=None, request=None):
        """
        Update an existing debt.
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({'id': 'Debt not found.'})
        
        # If borrower is changing, validate new borrower
        if data.get('borrower_id') and data['borrower_id'] != debt.borrower_id:
            borrower = Borrower.objects.filter(id=data['borrower_id']).first()
            if not borrower:
                raise ValidationError({'borrower_id': 'Borrower not found.'})
            debt.borrower = borrower
        
        # Update fields
        update_fields = ['name', 'total_amount', 'paid_amount', 'due_date', 
                        'status', 'interest_rate', 'penalty_rate', 
                        'interest_calculation_period', 'last_interest_accrual_date']
        for field in update_fields:
            if field in data:
                setattr(debt, field, data[field])
        
        # Recalculate remaining amount
        debt.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_update',
                model_name='Debt',
                object_id=str(debt.id),
                changes={'data': data}
            )
        
        logger.info(f"Debt updated: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def delete(debt_id, user=None, request=None):
        """
        Soft delete a debt.
        """
        debt = DebtService.get_by_id(debt_id)
        if not debt:
            raise ValidationError({'id': 'Debt not found.'})
        
        debt.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_delete',
                model_name='Debt',
                object_id=str(debt.id),
                changes={'deleted_at': debt.deleted_at}
            )
        
        logger.info(f"Debt soft-deleted: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    @transaction.atomic
    def restore(debt_id, user=None, request=None):
        """
        Restore a soft-deleted debt.
        """
        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            raise ValidationError({'id': 'Debt not found.'})
        if not debt.deleted_at:
            raise ValidationError({'id': 'Debt is not deleted.'})
        
        debt.restore()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_restore',
                model_name='Debt',
                object_id=str(debt.id),
                changes={'restored_at': timezone.now()}
            )
        
        logger.info(f"Debt restored: {debt.id} - {debt.name}")
        return debt

    @staticmethod
    def get_statistics():
        """
        Get debt statistics.
        """
        qs = Debt.objects.filter(deleted_at__isnull=True)
        
        total_debts = qs.count()
        total_active = qs.filter(status=Debt.Status.ACTIVE).count()
        total_paid = qs.filter(status=Debt.Status.PAID).count()
        total_overdue = qs.filter(status=Debt.Status.OVERDUE).count()
        total_defaulted = qs.filter(status=Debt.Status.DEFAULTED).count()
        
        total_amount = qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        remaining_amount = qs.aggregate(total=Sum('remaining_amount'))['total'] or Decimal('0')
        
        return {
            'total_debts': total_debts,
            'total_active': total_active,
            'total_paid': total_paid,
            'total_overdue': total_overdue,
            'total_defaulted': total_defaulted,
            'total_amount_owed': total_amount,
            'total_remaining_balance': remaining_amount,
        }

    @staticmethod
    def get_aging_summary(as_of_date=None):
        """
        Get aging summary for accounts receivable.
        """
        if not as_of_date:
            as_of_date = timezone.now().date()
        
        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=0
        ).select_related('borrower')
        
        buckets = {
            '0-30': {'total': Decimal('0'), 'count': 0},
            '31-60': {'total': Decimal('0'), 'count': 0},
            '61-90': {'total': Decimal('0'), 'count': 0},
            '90+': {'total': Decimal('0'), 'count': 0},
        }
        
        total_outstanding = Decimal('0')
        
        for debt in debts:
            if debt.due_date:
                days_past_due = (as_of_date - debt.due_date).days
                if days_past_due < 0:
                    days_past_due = 0
                
                if days_past_due <= 30:
                    bucket = '0-30'
                elif days_past_due <= 60:
                    bucket = '31-60'
                elif days_past_due <= 90:
                    bucket = '61-90'
                else:
                    bucket = '90+'
                
                buckets[bucket]['total'] += debt.remaining_amount
                buckets[bucket]['count'] += 1
                total_outstanding += debt.remaining_amount
        
        # Calculate percentages
        result = []
        for key, data in buckets.items():
            percentage = (data['total'] / total_outstanding * 100) if total_outstanding > 0 else 0
            result.append({
                'range': key,
                'total_amount': data['total'],
                'count': data['count'],
                'percentage': round(percentage, 2),
            })
        
        return {
            'as_of_date': as_of_date.isoformat(),
            'total_outstanding': total_outstanding,
            'buckets': result,
        }