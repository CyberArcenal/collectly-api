from datetime import timedelta
from django.utils import timezone
import logging
from typing import Any, Dict, Optional
from django.db import transaction
from django.core.exceptions import ValidationError

from audit.utils.log import log_audit_event
from debts.models.interest_rate_change_log import InterestRateChangeLog
from debts.models.debt import Debt
from system_settings.services.setting import SystemSettingService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class InterestRateChangeService:
    """
    Service for managing interest rate change logs.
    """

    @staticmethod
    def get_by_id(log_id):
        """
        Get a rate change log by ID.
        """
        try:
            return InterestRateChangeLog.objects.select_related('loan').get(id=log_id)
        except InterestRateChangeLog.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20):
        """
        Get paginated list of rate change logs.
        """
        qs = InterestRateChangeLog.objects.filter(deleted_at__isnull=True).order_by('-changed_at')
        
        if filters:
            if filters.get('setting_key'):
                qs = qs.filter(setting_key=filters['setting_key'])
            if filters.get('loan_id'):
                qs = qs.filter(loan_id=filters['loan_id'])
            if filters.get('changed_by'):
                qs = qs.filter(changed_by=filters['changed_by'])
            if filters.get('from_date'):
                qs = qs.filter(changed_at__gte=filters['from_date'])
            if filters.get('to_date'):
                qs = qs.filter(changed_at__lte=filters['to_date'])
        
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def create_log(setting_key, old_value, new_value, changed_by='system', reason=None, loan_id=None, user=None, request=None):
        """
        Create an interest rate change log.
        """
        log_entry = InterestRateChangeLog.objects.create(
            setting_key=setting_key,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
            reason=reason,
            loan_id=loan_id
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='interest_rate_change',
                model_name='InterestRateChangeLog',
                object_id=str(log_entry.id),
                changes={
                    'setting_key': setting_key,
                    'old_value': old_value,
                    'new_value': new_value,
                    'reason': reason,
                }
            )
        
        logger.info(f"Interest rate change logged: {setting_key} {old_value}->{new_value}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def update_system_rate(new_rate, changed_by='system', reason=None, user=None, request=None):
        """
        Update system-wide default interest rate.
        """
        old_rate = SystemSettingService.get_value('default_interest_rate', 'collections', 10)
        
        # Update system setting
        SystemSettingService.set_value(
            'default_interest_rate',
            new_rate,
            setting_type='collections',
            description='Default interest rate'
        )
        
        # Create log
        return InterestRateChangeService.create_log(
            setting_key='default_interest_rate',
            old_value=old_rate,
            new_value=new_rate,
            changed_by=changed_by,
            reason=reason,
            loan_id=None,
            user=user,
            request=request
        )

    @staticmethod
    @transaction.atomic
    def update_loan_rate(loan_id, new_rate, changed_by='system', reason=None, user=None, request=None):
        """
        Update interest rate for a specific loan.
        """
        loan = Debt.objects.filter(id=loan_id).first()
        if not loan:
            raise ValidationError({'loan_id': 'Debt not found.'})
        
        old_rate = loan.interest_rate
        
        # Update loan
        loan.interest_rate = new_rate
        loan.save()
        
        # Create log
        return InterestRateChangeService.create_log(
            setting_key=f'loan_{loan_id}',
            old_value=old_rate,
            new_value=new_rate,
            changed_by=changed_by,
            reason=reason,
            loan_id=loan_id,
            user=user,
            request=request
        )
        
    @staticmethod
    @transaction.atomic
    def update_system_rate(new_rate, changed_by='system', reason=None, user=None, request=None):
        """
        Update system-wide default interest rate and create log.
        
        Args:
            new_rate: New interest rate value
            changed_by: User making the change
            reason: Reason for the change
            user: User for audit
            request: Request object for audit
        """
        from system_settings.services.setting import SystemSettingService
        
        # Get current rate
        old_rate = SystemSettingService.get_value('default_interest_rate', 'collections', 10)
        
        # Update system setting
        SystemSettingService.set_value(
            'default_interest_rate',
            new_rate,
            setting_type='collections',
            description='Default interest rate'
        )
        
        # Create log entry
        return InterestRateChangeService.create_log(
            setting_key='default_interest_rate',
            old_value=old_rate,
            new_value=new_rate,
            changed_by=changed_by,
            reason=reason,
            loan_id=None,
            user=user,
            request=request
        )

    @staticmethod
    @transaction.atomic
    def update_loan_rate(loan_id, new_rate, changed_by='system', reason=None, user=None, request=None):
        """
        Update interest rate for a specific loan and create log.
        
        Args:
            loan_id: ID of the loan
            new_rate: New interest rate value
            changed_by: User making the change
            reason: Reason for the change
            user: User for audit
            request: Request object for audit
        """
        loan = Debt.objects.filter(id=loan_id).first()
        if not loan:
            raise ValidationError({'loan_id': 'Debt not found.'})
        
        old_rate = loan.interest_rate
        
        # Update loan
        loan.interest_rate = new_rate
        loan.save(update_fields=['interest_rate', 'updated_at'])
        
        # Create log entry
        return InterestRateChangeService.create_log(
            setting_key=f'loan_{loan_id}',
            old_value=old_rate,
            new_value=new_rate,
            changed_by=changed_by,
            reason=reason,
            loan_id=loan_id,
            user=user,
            request=request
        )
        

    @staticmethod
    def get_statistics(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get comprehensive statistics for interest rate change logs.
        
        Args:
            start_date: Optional start date (YYYY-MM-DD)
            end_date: Optional end date (YYYY-MM-DD)
        
        Returns:
            dict: Statistics including total changes, most frequent setting, etc.
        """
        from django.db.models import Count, Avg, Max, Min, Q, Value
        from django.db.models.functions import Abs
        
        qs = InterestRateChangeLog.objects.filter(deleted_at__isnull=True)
        
        # Apply date filters
        if start_date:
            qs = qs.filter(changed_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(changed_at__date__lte=end_date)
        
        total_changes = qs.count()
        
        if total_changes == 0:
            return {
                'total_changes': 0,
                'most_frequent_setting': None,
                'changes_by_user': [],
                'changes_by_loan': [],
                'average_change_magnitude': 0,
                'max_change_magnitude': 0,
                'min_change_magnitude': 0,
                'changes_last_30_days': 0,
            }
        
        # Most frequent setting_key
        most_frequent = qs.values('setting_key').annotate(
            count=Count('id')
        ).order_by('-count').first()
        
        most_frequent_setting = {
            'setting_key': most_frequent['setting_key'],
            'count': most_frequent['count'],
        } if most_frequent else None
        
        # Changes by user
        user_counts = qs.values('changed_by').annotate(
            count=Count('id')
        ).order_by('-count')
        
        changes_by_user = [
            {'user': item['changed_by'], 'count': item['count']}
            for item in user_counts
        ]
        
        # Changes by loan (only non-null)
        loan_counts = qs.filter(
            loan_id__isnull=False
        ).values('loan_id').annotate(
            count=Count('id')
        ).order_by('-count')
        
        changes_by_loan = [
            {'loan_id': item['loan_id'], 'count': item['count']}
            for item in loan_counts
        ]
        
        # Change magnitude statistics
        # Calculate absolute difference between old and new values
        # Filter out null values
        logs_with_values = qs.filter(
            old_value__isnull=False,
            new_value__isnull=False
        )
        
        if logs_with_values.exists():
            # Using Django's aggregation with expression
            from django.db.models import F, ExpressionWrapper, FloatField
            from django.db.models.functions import Abs
            
            magnitude_stats = logs_with_values.aggregate(
                avg_magnitude=Avg(
                    ExpressionWrapper(
                        Abs(F('new_value') - F('old_value')),
                        output_field=FloatField()
                    )
                ),
                max_magnitude=Max(
                    ExpressionWrapper(
                        Abs(F('new_value') - F('old_value')),
                        output_field=FloatField()
                    )
                ),
                min_magnitude=Min(
                    ExpressionWrapper(
                        Abs(F('new_value') - F('old_value')),
                        output_field=FloatField()
                    )
                ),
            )
            
            average_change_magnitude = magnitude_stats['avg_magnitude'] or 0
            max_change_magnitude = magnitude_stats['max_magnitude'] or 0
            min_change_magnitude = magnitude_stats['min_magnitude'] or 0
        else:
            average_change_magnitude = 0
            max_change_magnitude = 0
            min_change_magnitude = 0
        
        # Changes in last 30 days
        thirty_days_ago = timezone.now() - timedelta(days=30)
        changes_last_30_days = qs.filter(changed_at__gte=thirty_days_ago).count()
        
        return {
            'total_changes': total_changes,
            'most_frequent_setting': most_frequent_setting,
            'changes_by_user': changes_by_user,
            'changes_by_loan': changes_by_loan,
            'average_change_magnitude': round(float(average_change_magnitude), 2),
            'max_change_magnitude': round(float(max_change_magnitude), 2),
            'min_change_magnitude': round(float(min_change_magnitude), 2),
            'changes_last_30_days': changes_last_30_days,
        }