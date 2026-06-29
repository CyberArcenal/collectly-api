import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from debts.models.debt import Debt
from system_settings.services.setting import SystemSettingService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class InterestAccrualService:
    """
    Service for calculating and applying interest to debts.
    """

    @staticmethod
    def apply_accrual(debt, as_of_date=None):
        """
        Apply interest accrual to a single debt.
        """
        if not as_of_date:
            as_of_date = timezone.now().date()
        
        # Skip if debt is not active or overdue
        if debt.status not in [Debt.Status.ACTIVE, Debt.Status.OVERDUE]:
            return debt
        
        # Skip if no interest rate
        if not debt.interest_rate or debt.interest_rate <= 0:
            return debt
        
        # Skip if fully paid
        if debt.remaining_amount <= Decimal('0.01'):
            return debt
        
        # Get last accrual date
        last_date = debt.last_interest_accrual_date or debt.created_at.date()
        if last_date >= as_of_date:
            return debt
        
        # Calculate days difference
        days_diff = (as_of_date - last_date).days
        if days_diff <= 0:
            return debt
        
        # Calculate daily rate
        if debt.interest_calculation_period == Debt.InterestPeriod.PER_MONTH:
            daily_rate = (debt.interest_rate / 100) / 30
        else:  # PER_ANNUUM
            daily_rate = (debt.interest_rate / 100) / 365
        
        # Get amortization type
        amort_type = SystemSettingService.get_value('amortization_type', 'loans', 'flat')
        
        # Calculate principal based on amortization type
        if amort_type == 'flat':
            principal = debt.total_amount
        else:  # declining
            principal = debt.remaining_amount
        
        # Calculate interest
        interest_amount = principal * daily_rate * days_diff
        
        if interest_amount <= Decimal('0.01'):
            return debt
        
        # Update debt
        old_remaining = debt.remaining_amount
        debt.remaining_amount += interest_amount
        debt.last_interest_accrual_date = as_of_date
        debt.save()
        
        logger.info(
            f"Interest accrued for debt #{debt.id}: ₱{interest_amount:.2f} "
            f"for {days_diff} days ({amort_type})"
        )
        
        # Audit log
        log_audit_event(
            request=None,
            user='system',
            action_type='interest_accrual',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'old_remaining': old_remaining,
                'interest_amount': interest_amount,
                'new_remaining': debt.remaining_amount,
                'days': days_diff,
                'amortization_type': amort_type,
            }
        )
        
        return debt

    @staticmethod
    def run_daily_accrual():
        """
        Run interest accrual for all eligible debts.
        """
        today = timezone.now().date()
        
        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=0,
            interest_rate__isnull=False,
            interest_rate__gt=0
        )
        
        processed = 0
        errors = 0
        
        for debt in debts:
            try:
                InterestAccrualService.apply_accrual(debt, today)
                processed += 1
            except Exception as e:
                logger.error(f"Error accruing interest for debt #{debt.id}: {e}")
                errors += 1
        
        logger.info(f"Interest accrual completed: {processed} processed, {errors} errors")
        return {'processed': processed, 'errors': errors}