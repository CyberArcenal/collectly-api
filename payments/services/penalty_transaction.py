import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.penalty_transaction import PenaltyTransaction
from debts.models.debt import Debt
from system_settings.services.setting import SystemSettingService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class PenaltyTransactionService:
    """
    Service layer for PenaltyTransaction CRUD operations.
    """

    @staticmethod
    def get_by_id(penalty_id, include_deleted=False):
        """
        Get a single penalty by ID.
        """
        qs = PenaltyTransaction.objects.select_related('debt')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=penalty_id)
        except PenaltyTransaction.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='penalty_date', sort_order='desc'):
        """
        Get paginated list of penalties with filters.
        """
        qs = PenaltyTransaction.objects.select_related('debt').filter(deleted_at__isnull=True)
        
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
            if filters.get('include_deleted'):
                qs = PenaltyTransaction.objects.select_related('debt')
        
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
        Create a new penalty transaction.
        """
        debt = Debt.objects.filter(id=data['debt_id']).first()
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})
        
        # Validate penalty amount
        if data['amount'] <= 0:
            raise ValidationError({'amount': 'Penalty amount must be positive.'})
        
        penalty = PenaltyTransaction.objects.create(
            debt=debt,
            amount=data['amount'],
            penalty_date=data.get('penalty_date', timezone.now().date()),
            reason=data.get('reason'),
            is_auto=data.get('is_auto', False)
        )
        
        # Update debt remaining amount
        debt.remaining_amount += penalty.amount
        debt.save()
        
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
    def delete(penalty_id, user=None, request=None):
        """
        Soft delete a penalty.
        """
        penalty = PenaltyTransactionService.get_by_id(penalty_id)
        if not penalty:
            raise ValidationError({'id': 'Penalty not found.'})
        
        # Reverse penalty amount from debt
        debt = penalty.debt
        debt.remaining_amount -= penalty.amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.save()
        
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
    def get_statistics():
        """
        Get penalty statistics.
        """
        qs = PenaltyTransaction.objects.filter(deleted_at__isnull=True)
        
        total_penalties = qs.count()
        total_amount = qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        average_amount = total_amount / total_penalties if total_penalties > 0 else 0
        
        # Auto vs manual
        auto_count = qs.filter(is_auto=True).count()
        manual_count = qs.filter(is_auto=False).count()
        
        # Last 30 days
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent = qs.filter(penalty_date__gte=thirty_days_ago).count()
        
        return {
            'total_penalties': total_penalties,
            'total_penalty_amount': total_amount,
            'average_penalty_amount': average_amount,
            'auto_generated': auto_count,
            'manual': manual_count,
            'penalties_last_30_days': recent,
        }

    @staticmethod
    def run_auto_penalties():
        """
        Run auto-penalty for overdue debts.
        """
        enable_auto = SystemSettingService.get_value('enable_auto_penalty', 'collections', True)
        if not enable_auto:
            return {'processed': 0, 'errors': 0, 'message': 'Auto-penalty disabled'}
        
        grace_days = SystemSettingService.get_value('penalty_grace_days', 'collections', 0)
        penalty_rate = SystemSettingService.get_value('default_penalty_rate', 'collections', 2)
        calc_method = SystemSettingService.get_value('penalty_calculation_method', 'collections', 'percentage')
        
        today = timezone.now().date()
        
        # Find overdue debts
        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=0,
            due_date__lt=today
        )
        
        processed = 0
        errors = 0
        
        for debt in debts:
            # Check grace period
            if grace_days > 0:
                days_overdue = (today - debt.due_date).days
                if days_overdue <= grace_days:
                    continue
            
            # Check if penalty already applied today
            existing = PenaltyTransaction.objects.filter(
                debt=debt,
                deleted_at__isnull=True,
                penalty_date=today
            ).exists()
            
            if existing:
                continue
            
            try:
                # Calculate penalty
                if calc_method == 'percentage':
                    penalty_amount = debt.remaining_amount * (penalty_rate / 100)
                else:  # fixed
                    penalty_amount = Decimal(str(penalty_rate))
                
                if penalty_amount <= 0:
                    continue
                
                # Create penalty
                PenaltyTransactionService.create(
                    data={
                        'debt_id': debt.id,
                        'amount': penalty_amount,
                        'penalty_date': today,
                        'reason': f'Auto-penalty for overdue debt',
                        'is_auto': True,
                    },
                    user='system'
                )
                processed += 1
                
            except Exception as e:
                logger.error(f"Error applying auto-penalty to debt #{debt.id}: {e}")
                errors += 1
        
        logger.info(f"Auto-penalty completed: {processed} processed, {errors} errors")
        return {'processed': processed, 'errors': errors}