import logging
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