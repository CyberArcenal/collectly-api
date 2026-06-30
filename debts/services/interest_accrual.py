import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from datetime import datetime, date
from django.db.models import Sum
from debts.models.debt import Debt
from system_settings.utils import amortization_type
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class InterestAccrualService:
    """
    Service for calculating and applying interest to debts.
    
    Handles daily interest accrual for active and overdue debts using
    either flat or declining balance amortization methods.
    """

    @staticmethod
    def apply_accrual(debt:Debt, as_of_date=None):
        """
        Apply interest accrual to a single debt up to a specified date.

        Args:
            debt: Debt instance to accrue interest for
            as_of_date: Date to accrue up to (defaults to today)

        Returns:
            Debt: The updated debt instance (or original if no accrual applied)

        Raises:
            ValueError: If debt is in an invalid state for accrual
        """
        # Parse and normalize as_of_date
        if as_of_date is None:
            as_of_date = timezone.now().date()
        elif isinstance(as_of_date, str):
            try:
                as_of_date = datetime.fromisoformat(as_of_date).date()
            except ValueError:
                raise ValueError(f"Invalid date format: {as_of_date}")
        elif isinstance(as_of_date, datetime):
            as_of_date = as_of_date.date()

        # Skip if debt is not active or overdue
        if debt.status not in [Debt.Status.ACTIVE, Debt.Status.OVERDUE]:
            logger.debug(
                f"[InterestAccrual] Skipping debt #{debt.id}, status: {debt.status}"
            )
            return debt

        # Skip if no interest rate
        if not debt.interest_rate or debt.interest_rate <= 0:
            logger.debug(
                f"[InterestAccrual] Skipping debt #{debt.id}, interest_rate = {debt.interest_rate}"
            )
            return debt

        # Skip if fully paid
        if debt.remaining_amount <= Decimal('0.01'):
            logger.debug(
                f"[InterestAccrual] Skipping debt #{debt.id}, remaining = {debt.remaining_amount}"
            )
            return debt

        # Get last accrual date
        last_date = debt.last_interest_accrual_date
        if last_date is None:
            last_date = debt.created_at.date()
        
        # Ensure last_date is a date object
        if isinstance(last_date, datetime):
            last_date = last_date.date()

        # Check if already accrued up to this date
        if last_date >= as_of_date:
            logger.debug(
                f"[InterestAccrual] Debt #{debt.id} already accrued up to {last_date}"
            )
            return debt

        # Calculate days difference
        days_diff = (as_of_date - last_date).days
        if days_diff <= 0:
            return debt

        # Calculate daily interest rate
        if debt.interest_calculation_period == Debt.InterestPeriod.PER_MONTH:
            # 30-day month assumption
            daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('30')
        else:  # PER_ANNUUM
            daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('365')

        # Get amortization type from system settings
        amort_type = amortization_type()

        # Calculate principal based on amortization type
        if amort_type == 'flat':
            # Flat rate: interest on original principal
            principal = debt.total_amount
        else:
            # Declining balance: interest on remaining amount
            principal = debt.remaining_amount

        # Calculate interest amount
        interest_amount = principal * daily_rate * days_diff

        # Skip negligible interest
        if interest_amount <= Decimal('0.01'):
            logger.debug(
                f"[InterestAccrual] Negligible interest for debt #{debt.id}: {interest_amount}"
            )
            return debt

        # Store old value for audit
        old_remaining = debt.remaining_amount

        # Update debt
        new_remaining = debt.remaining_amount + interest_amount
        debt.remaining_amount = Decimal(str(round(new_remaining, 2)))
        debt.last_interest_accrual_date = as_of_date
        debt.save(update_fields=['remaining_amount', 'last_interest_accrual_date', 'updated_at'])

        logger.info(
            f"[InterestAccrual] Debt #{debt.id}: +{interest_amount:.2f} interest "
            f"({amort_type}) for {days_diff} day(s). "
            f"New remaining: {debt.remaining_amount:.2f}"
        )

        # Audit log
        log_audit_event(
            request=None,
            user='system',
            action_type='interest_accrual',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'old_remaining': float(old_remaining),
                'interest_amount': float(interest_amount),
                'new_remaining': float(debt.remaining_amount),
                'days': days_diff,
                'amortization_type': amort_type,
                'accrual_period': debt.interest_calculation_period,
                'daily_rate': float(daily_rate),
                'principal': float(principal),
            }
        )

        return debt

    @staticmethod
    def find_eligible_debts(as_of_date=None):
        """
        Find all debts eligible for interest accrual.

        Args:
            as_of_date: Date to check eligibility for (defaults to today)

        Returns:
            QuerySet: Debts eligible for interest accrual
        """
        if as_of_date is None:
            as_of_date = timezone.now().date()
        elif isinstance(as_of_date, str):
            try:
                as_of_date = datetime.fromisoformat(as_of_date).date()
            except ValueError:
                raise ValueError(f"Invalid date format: {as_of_date}")

        return Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal('0.01'),
            interest_rate__isnull=False,
            interest_rate__gt=0
        )

    @staticmethod
    def run_daily_accrual():
        """
        Run interest accrual for all eligible debts up to today.

        This is the main entry point for the daily scheduler.

        Returns:
            dict: {
                'processed': int,    # Number of debts successfully accrued
                'errors': int,      # Number of debts that failed
                'skipped': int,     # Number of debts skipped (optional)
                'total_interest': Decimal  # Total interest accrued
            }
        """
        today = timezone.now().date()
        logger.info("[InterestAccrual] Starting daily interest accrual...")

        debts = InterestAccrualService.find_eligible_debts(today)
        total = debts.count()

        if total == 0:
            logger.info("[InterestAccrual] No debts need accrual.")
            return {
                'processed': 0,
                'errors': 0,
                'skipped': 0,
                'total_interest': Decimal('0'),
            }

        processed = 0
        errors = 0
        skipped = 0
        total_interest = Decimal('0')

        for debt in debts:
            try:
                # Check if already accrued today
                if debt.last_interest_accrual_date == today:
                    skipped += 1
                    continue

                result = InterestAccrualService.apply_accrual(debt, today)
                
                # Check if interest was actually applied
                if result and result.last_interest_accrual_date == today:
                    processed += 1
                    # Calculate interest added (approximate)
                    # We could track this more accurately in the audit log
            except Exception as e:
                logger.error(f"[InterestAccrual] Failed for debt #{debt.id}: {e}")
                errors += 1

        logger.info(
            f"[InterestAccrual] Completed: {processed} processed, "
            f"{errors} errors, {skipped} skipped"
        )

        return {
            'processed': processed,
            'errors': errors,
            'skipped': skipped,
            'total_interest': total_interest,
        }

    @staticmethod
    def accrue_for_payment(debt_id, payment_date=None):
        """
        Accrue interest for a specific debt before processing a payment.

        This should be called BEFORE applying a payment to ensure the
        balance is accurate.

        Args:
            debt_id: ID of the debt
            payment_date: Date of the payment (defaults to today)

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If debt not found
        """
        from django.core.exceptions import ValidationError

        if payment_date is None:
            payment_date = timezone.now().date()
        elif isinstance(payment_date, str):
            try:
                payment_date = datetime.fromisoformat(payment_date).date()
            except ValueError:
                raise ValueError(f"Invalid date format: {payment_date}")

        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            raise ValidationError({'debt_id': f'Debt #{debt_id} not found.'})

        return InterestAccrualService.apply_accrual(debt, payment_date)

    @staticmethod
    def get_accrual_forecast(debt:Debt, days=30):
        """
        Get interest accrual forecast for a debt over a specified period.

        Args:
            debt: Debt instance
            days: Number of days to forecast

        Returns:
            dict: {
                'current_balance': Decimal,
                'projected_interest': Decimal,
                'projected_balance': Decimal,
                'daily_rate': Decimal,
                'days': int,
                'amortization_type': str,
            }
        """
        if not debt.interest_rate or debt.interest_rate <= 0:
            return {
                'current_balance': debt.remaining_amount,
                'projected_interest': Decimal('0'),
                'projected_balance': debt.remaining_amount,
                'daily_rate': Decimal('0'),
                'days': days,
                'amortization_type': 'none',
            }

        # Calculate daily rate
        if debt.interest_calculation_period == Debt.InterestPeriod.PER_MONTH:
            daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('30')
        else:
            daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('365')

        # Get amortization type
        amort_type = amortization_type()

        # Calculate principal
        if amort_type == 'flat':
            principal = debt.total_amount
        else:
            principal = debt.remaining_amount

        projected_interest = principal * daily_rate * days
        projected_balance = debt.remaining_amount + projected_interest

        return {
            'current_balance': debt.remaining_amount,
            'projected_interest': round(projected_interest, 2),
            'projected_balance': round(projected_balance, 2),
            'daily_rate': daily_rate,
            'days': days,
            'amortization_type': amort_type,
            'principal_used': principal,
        }

    @staticmethod
    def get_daily_accrual_summary(date=None):
        """
        Get summary of interest accrual for a specific date.

        Args:
            date: Date to summarize (defaults to today)

        Returns:
            dict: Summary of accrual activity
        """
        if date is None:
            date = timezone.now().date()
        elif isinstance(date, str):
            try:
                date = datetime.fromisoformat(date).date()
            except ValueError:
                raise ValueError(f"Invalid date format: {date}")

        debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            remaining_amount__gt=Decimal('0.01'),
            interest_rate__isnull=False,
            interest_rate__gt=0
        )

        total_eligible = debts.count()
        total_remaining = debts.aggregate(total=Sum('remaining_amount'))['total'] or Decimal('0')

        # Calculate potential daily interest (approximate)
        daily_rates = []
        for debt in debts:
            if debt.interest_calculation_period == Debt.InterestPeriod.PER_MONTH:
                daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('30')
            else:
                daily_rate = (debt.interest_rate / Decimal('100')) / Decimal('365')

            amort_type = amortization_type()
            principal = debt.total_amount if amort_type == 'flat' else debt.remaining_amount
            daily_interest = principal * daily_rate
            daily_rates.append(daily_interest)

        total_daily_interest = sum(daily_rates) if daily_rates else Decimal('0')

        return {
            'date': date.isoformat(),
            'eligible_debts': total_eligible,
            'total_remaining_balance': total_remaining,
            'projected_daily_interest': round(total_daily_interest, 2),
            'amortization_type': amortization_type(),
        }