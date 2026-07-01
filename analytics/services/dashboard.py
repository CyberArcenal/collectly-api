# analytics/services/dashboard.py
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from django.db.models import Q, Sum, Count
from django.utils import timezone

from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from payment_methods.models.payment_method import PaymentMethod
from audit.models.log import AuditLog

logger = logging.getLogger(__name__)


class DashboardService:
    """
    Service layer for dashboard/analytics operations.
    All methods are read-only and aggregate data from other apps.
    """

    # ============================================================
    # OVERVIEW
    # ============================================================

    @staticmethod
    def get_overview():
        """
        Get dashboard overview data.
        
        Returns:
            dict: {
                'todayRevenue': float,
                'totalCustomers': int,
                'activeDebts': int,
                'overdueDebts': int
            }
        """
        today = timezone.now().date()
        tomorrow = today + timedelta(days=1)

        # Today's revenue
        today_revenue = PaymentTransaction.objects.filter(
            deleted_at__isnull=True,
            payment_date__gte=today,
            payment_date__lt=tomorrow
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Total customers (active borrowers)
        total_customers = Borrower.objects.filter(
            deleted_at__isnull=True
        ).count()

        # Active debts
        active_debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).count()

        # Overdue debts
        overdue_debts = Debt.objects.filter(
            deleted_at__isnull=True,
            status=Debt.Status.OVERDUE
        ).count()

        return {
            'todayRevenue': float(today_revenue),
            'totalCustomers': total_customers,
            'activeDebts': active_debts,
            'overdueDebts': overdue_debts,
        }


    # ============================================================
    # REVENUE
    # ============================================================

    @staticmethod
    def get_revenue(period='month', start_date=None, end_date=None):
        """
        Get revenue data for a given period.
        
        Args:
            period: 'today', 'week', 'month', 'year'
            start_date: Optional custom start date (ISO string)
            end_date: Optional custom end date (ISO string)
        
        Returns:
            dict: {
                'totalRevenue': float,
                'transactionCount': int,
                'period': str
            }
        """
        now = timezone.now()
        qs = PaymentTransaction.objects.filter(deleted_at__isnull=True)

        if start_date and end_date:
            qs = qs.filter(
                payment_date__gte=start_date,
                payment_date__lte=end_date
            )
            period_label = f"{start_date} to {end_date}"
        elif period == 'today':
            today = now.date()
            tomorrow = today + timedelta(days=1)
            qs = qs.filter(
                payment_date__gte=today,
                payment_date__lt=tomorrow
            )
            period_label = 'today'
        elif period == 'week':
            start = now.date() - timedelta(days=7)
            qs = qs.filter(payment_date__gte=start)
            period_label = 'week'
        elif period == 'year':
            start = now.date() - timedelta(days=365)
            qs = qs.filter(payment_date__gte=start)
            period_label = 'year'
        else:  # month (default)
            start = now.date() - timedelta(days=30)
            qs = qs.filter(payment_date__gte=start)
            period_label = 'month'

        stats = qs.aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        return {
            'totalRevenue': float(stats['total'] or Decimal('0')),
            'transactionCount': stats['count'] or 0,
            'period': period_label,
        }


    # ============================================================
    # STATISTICS
    # ============================================================

    @staticmethod
    def get_statistics():
        """
        Get comprehensive dashboard statistics.
        
        Returns:
            dict: {
                'totalBorrowers': int,
                'totalDebts': int,
                'totalPaidDebts': int,
                'totalOverdue': int,
                'totalPaymentsCollected': float,
                'totalPenaltiesCollected': float,
                'totalRemainingBalance': float
            }
        """
        # Borrowers
        total_borrowers = Borrower.objects.filter(deleted_at__isnull=True).count()

        # Debts
        total_debts = Debt.objects.filter(deleted_at__isnull=True).count()
        total_paid = Debt.objects.filter(
            deleted_at__isnull=True,
            status=Debt.Status.PAID
        ).count()
        total_overdue = Debt.objects.filter(
            deleted_at__isnull=True,
            status=Debt.Status.OVERDUE
        ).count()

        # Payments
        payments_total = PaymentTransaction.objects.filter(
            deleted_at__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Penalties
        penalties_total = PenaltyTransaction.objects.filter(
            deleted_at__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Remaining balance
        remaining = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).aggregate(total=Sum('remaining_amount'))['total'] or Decimal('0')

        return {
            'totalBorrowers': total_borrowers,
            'totalDebts': total_debts,
            'totalPaidDebts': total_paid,
            'totalOverdue': total_overdue,
            'totalPaymentsCollected': float(payments_total),
            'totalPenaltiesCollected': float(penalties_total),
            'totalRemainingBalance': float(remaining),
        }


    # ============================================================
    # TOP PRODUCTS (Top Debts by Amount)
    # ============================================================

    @staticmethod
    def get_top_products(limit=5):
        """
        Get top debts by total amount.
        
        Args:
            limit: Number of top products to return
        
        Returns:
            list: [{'name': str, 'totalValue': float}, ...]
        """
        top_debts = Debt.objects.filter(
            deleted_at__isnull=True
        ).values('name').annotate(
            total_value=Sum('total_amount')
        ).order_by('-total_value')[:limit]

        return [
            {
                'name': item['name'],
                'totalValue': float(item['total_value'] or 0)
            }
            for item in top_debts
        ]


    # ============================================================
    # LOW STOCK / DUE SOON
    # ============================================================

    @staticmethod
    def get_low_stock(threshold=5):
        """
        Get debts that are due soon (within threshold days).
        
        Args:
            threshold: Number of days to look ahead
        
        Returns:
            list: [{'id': int, 'name': str, 'dueDate': date}, ...]
        """
        today = timezone.now().date()
        future_date = today + timedelta(days=threshold)

        due_soon = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            due_date__gte=today,
            due_date__lte=future_date,
            remaining_amount__gt=0
        ).values('id', 'name', 'due_date').order_by('due_date')[:10]

        return [
            {
                'id': item['id'],
                'name': item['name'],
                'dueDate': item['due_date'],
            }
            for item in due_soon
        ]


    # ============================================================
    # RECENT ACTIVITIES
    # ============================================================

    @staticmethod
    def get_recent_activities(limit=10):
        """
        Get recent activities (combines audit logs and payments).
        
        Args:
            limit: Number of activities to return
        
        Returns:
            list: [{'id', 'action', 'entity', 'entityId', 'user', 'timestamp', 'details'}, ...]
        """
        activities = []

        # Get recent audit logs
        audit_logs = AuditLog.objects.all().select_related('user').order_by('-timestamp')[:limit]

        for log in audit_logs:
            activities.append({
                'id': f"audit_{log.id}",
                'action': log.action_type.upper(),
                'entity': log.model_name,
                'entityId': int(log.object_id) if log.object_id and log.object_id.isdigit() else None,
                'user': log.user.username if log.user else 'system',
                'timestamp': log.timestamp,
                'details': f"{log.action_type} on {log.model_name}",
            })

        # Get recent payments
        payments = PaymentTransaction.objects.filter(
            deleted_at__isnull=True
        ).select_related('debt', 'debt__borrower').order_by('-payment_date')[:limit]

        for payment in payments:
            borrower_name = payment.debt.borrower.name if payment.debt and payment.debt.borrower else 'Unknown'
            activities.append({
                'id': f"payment_{payment.id}",
                'action': 'PAYMENT',
                'entity': 'PaymentTransaction',
                'entityId': payment.id,
                'user': payment.recorded_by.username if payment.recorded_by else 'system',
                'timestamp': payment.payment_date,
                'details': f"Payment of ₱{payment.amount:,.2f} by {borrower_name}",
            })

        # Sort by timestamp (most recent first) and limit
        activities.sort(key=lambda x: x['timestamp'], reverse=True)
        return activities[:limit]


    # ============================================================
    # SALES TREND
    # ============================================================

    @staticmethod
    def get_sales_trend(days=7):
        """
        Get daily sales trend over a number of days.
        
        Args:
            days: Number of past days to include
        
        Returns:
            list: [{'date': date, 'total': float}, ...]
        """
        start_date = timezone.now().date() - timedelta(days=days)
        start_date = datetime.combine(start_date, datetime.min.time())

        payments = PaymentTransaction.objects.filter(
            deleted_at__isnull=True,
            payment_date__gte=start_date
        )

        # Group by date
        from django.db.models.functions import TruncDate
        trend = payments.annotate(
            date=TruncDate('payment_date')
        ).values('date').annotate(
            total=Sum('amount')
        ).order_by('date')

        # Fill in missing dates with zero
        result = []
        current = start_date.date()
        end_date = timezone.now().date()
        trend_dict = {item['date']: float(item['total'] or 0) for item in trend}

        while current <= end_date:
            result.append({
                'date': current.isoformat(),
                'total': trend_dict.get(current, 0)
            })
            current += timedelta(days=1)

        return result


    # ============================================================
    # PAYMENT METHODS BREAKDOWN
    # ============================================================

    @staticmethod
    def get_payment_methods_breakdown():
        """
        Get payment methods breakdown with usage statistics.
        
        Returns:
            list: [{'method': str, 'count': int, 'total': float}, ...]
        """
        # Group by method name
        breakdown = PaymentTransaction.objects.filter(
            deleted_at__isnull=True,
            method__isnull=False
        ).values('method__name').annotate(
            count=Count('id'),
            total=Sum('amount')
        ).order_by('-total')

        result = []
        for item in breakdown:
            result.append({
                'method': item['method__name'] or 'Unknown',
                'count': item['count'],
                'total': float(item['total'] or 0),
            })

        # If no transactions, return default
        if not result:
            # Get total cash transactions (or default)
            total = PaymentTransaction.objects.filter(
                deleted_at__isnull=True
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            result.append({
                'method': 'Cash',
                'count': 0,
                'total': float(total),
            })

        return result