import logging
from typing import Optional, Dict, Any
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Avg, Count, Min, Max
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.models.borrower import Borrower
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class CreditCheckService:
    """
    Service layer for CreditCheckLog operations.

    Handles creation, retrieval, and deletion of credit check logs.
    Also manages credit score calculations and statistics.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(log_id: int) -> Optional[CreditCheckLog]:
        """
        Get a single credit check log by ID.

        Args:
            log_id: ID of the credit check log to retrieve

        Returns:
            CreditCheckLog instance or None if not found
        """
        try:
            return CreditCheckLog.objects.get(id=log_id)
        except CreditCheckLog.DoesNotExist:
            return None

    @staticmethod
    def get_by_borrower(borrower_id: int, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """
        Get paginated credit check history for a borrower.

        Args:
            borrower_id: ID of the borrower
            page: Page number for pagination
            limit: Number of items per page

        Returns:
            dict: Paginated list of credit checks
        """
        qs = CreditCheckLog.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True
        ).order_by('-date_checked')

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_latest(borrower_id: int) -> Optional[CreditCheckLog]:
        """
        Get the most recent credit check for a borrower.

        Args:
            borrower_id: ID of the borrower

        Returns:
            CreditCheckLog instance or None if not found
        """
        return CreditCheckLog.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True
        ).order_by('-date_checked').first()

    @staticmethod
    def get_latest_valid(borrower_id: int, validity_days: int = 30) -> Optional[CreditCheckLog]:
        """
        Get the most recent valid credit check for a borrower.

        Args:
            borrower_id: ID of the borrower
            validity_days: Number of days the credit check is valid

        Returns:
            CreditCheckLog instance or None if no valid check found
        """
        cutoff_date = timezone.now() - timedelta(days=validity_days)

        return CreditCheckLog.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True,
            date_checked__gte=cutoff_date
        ).order_by('-date_checked').first()

    @staticmethod
    def get_statistics(borrower_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get credit check statistics.

        Args:
            borrower_id: Optional borrower ID to filter by

        Returns:
            dict: Statistics including average score and risk distribution
        """
        qs = CreditCheckLog.objects.filter(deleted_at__isnull=True)
        if borrower_id:
            qs = qs.filter(debtor_id=borrower_id)

        total = qs.count()

        if total == 0:
            return {
                'total': 0,
                'average_score': 0,
                'excellent_count': 0,
                'passing_count': 0,
                'risk_distribution': [],
                'score_range': {'min': 0, 'max': 0},
            }

        # Average score
        avg_score = qs.aggregate(avg=Avg('score'))['avg'] or 0

        # Score range
        score_range = qs.aggregate(min=Min('score'), max=Max('score'))

        # Risk level distribution
        risk_distribution = qs.values('risk_level').annotate(
            count=Count('id')
        ).order_by('risk_level')

        # Excellent scores (>= 750)
        excellent = qs.filter(score__gte=750).count()

        # Passing scores (>= 600)
        passing = qs.filter(score__gte=600).count()

        # Good scores (>= 700)
        good = qs.filter(score__gte=700, score__lt=750).count()

        # Fair scores (500-699)
        fair = qs.filter(score__gte=500, score__lt=700).count()

        # Poor scores (< 500)
        poor = qs.filter(score__lt=500).count()

        return {
            'total': total,
            'average_score': round(avg_score, 2),
            'excellent_count': excellent,
            'good_count': good,
            'fair_count': fair,
            'poor_count': poor,
            'passing_count': passing,
            'score_range': {
                'min': score_range['min'] or 0,
                'max': score_range['max'] or 0,
            },
            'risk_distribution': list(risk_distribution),
        }

    @staticmethod
    def get_borrower_credit_summary(borrower_id: int) -> Dict[str, Any]:
        """
        Get a comprehensive credit summary for a borrower.

        Args:
            borrower_id: ID of the borrower

        Returns:
            dict: Credit summary with latest check and historical stats
        """
        borrower = Borrower.objects.filter(id=borrower_id).first()
        if not borrower:
            raise ValidationError({'borrower_id': 'Borrower not found.'})

        latest = CreditCheckService.get_latest(borrower_id)
        stats = CreditCheckService.get_statistics(borrower_id)

        return {
            'borrower_id': borrower_id,
            'borrower_name': borrower.name,
            'latest_credit_check': {
                'score': latest.score if latest else None,
                'risk_level': latest.risk_level if latest else None,
                'date_checked': latest.date_checked.isoformat() if latest else None,
            } if latest else None,
            'total_checks': stats['total'],
            'average_score': stats['average_score'],
            'highest_score': stats['score_range']['max'],
            'lowest_score': stats['score_range']['min'],
            'risk_distribution': stats['risk_distribution'],
            'is_passing': latest and latest.is_passing,
            'is_excellent': latest and latest.is_excellent,
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user=None, request=None) -> CreditCheckLog:
        """
        Create a new credit check log.

        Args:
            data: Dictionary containing credit check data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            CreditCheckLog: The created credit check instance

        Raises:
            ValidationError: If validation fails
        """
        debtor_id = data.get('debtor_id')
        if not debtor_id:
            raise ValidationError({'debtor_id': 'Debtor ID is required.'})

        debtor = Borrower.objects.filter(id=debtor_id).first()
        if not debtor:
            raise ValidationError({'debtor_id': 'Borrower not found.'})

        # Get score and validate
        score = data.get('score', 0)
        if not (300 <= score <= 850):
            raise ValidationError({'score': 'Score must be between 300 and 850.'})

        # Auto-calculate risk level based on score
        risk_level = data.get('risk_level')
        if not risk_level:
            if score >= 700:
                risk_level = CreditCheckLog.RiskLevel.LOW
            elif score >= 500:
                risk_level = CreditCheckLog.RiskLevel.MEDIUM
            else:
                risk_level = CreditCheckLog.RiskLevel.HIGH

        # Check if there's already a check today (optional guardrail)
        today = timezone.now().date()
        existing_today = CreditCheckLog.objects.filter(
            debtor=debtor,
            date_checked__date=today,
            deleted_at__isnull=True
        ).exists()

        if existing_today:
            logger.warning(f"Credit check already performed for borrower {debtor_id} today")

        # Create log entry
        log_entry = CreditCheckLog.objects.create(
            debtor=debtor,
            score=score,
            risk_level=risk_level,
            remarks=data.get('remarks'),
            performed_by=data.get('performed_by'),
            external_reference=data.get('external_reference'),
            date_checked=timezone.now()
        )

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='credit_check_performed',
                model_name='CreditCheckLog',
                object_id=str(log_entry.id),
                changes={
                    'debtor_id': debtor.id,
                    'score': score,
                    'risk_level': risk_level,
                }
            )

        logger.info(f"Credit check performed for borrower {debtor.id}: score={score}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def delete(log_id: int, user=None, request=None) -> CreditCheckLog:
        """
        Soft delete a credit check log.

        Args:
            log_id: ID of the credit check log to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            CreditCheckLog: The soft-deleted credit check instance

        Raises:
            ValidationError: If credit check not found or already deleted
        """
        log_entry = CreditCheckService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Credit check log not found.'})

        if log_entry.deleted_at:
            raise ValidationError({'id': 'Credit check log is already deleted.'})

        log_entry.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='credit_check_delete',
                model_name='CreditCheckLog',
                object_id=str(log_entry.id),
                changes={'deleted_at': log_entry.deleted_at}
            )

        logger.info(f"Credit check log deleted: {log_id}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def restore(log_id: int, user=None, request=None) -> CreditCheckLog:
        """
        Restore a soft-deleted credit check log.

        Args:
            log_id: ID of the credit check log to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            CreditCheckLog: The restored credit check instance

        Raises:
            ValidationError: If credit check not found or not deleted
        """
        log_entry = CreditCheckLog.objects.filter(id=log_id).first()
        if not log_entry:
            raise ValidationError({'id': 'Credit check log not found.'})

        if not log_entry.deleted_at:
            raise ValidationError({'id': 'Credit check log is not deleted.'})

        log_entry.restore()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='credit_check_restore',
                model_name='CreditCheckLog',
                object_id=str(log_entry.id),
                changes={'restored_at': timezone.now()}
            )

        logger.info(f"Credit check log restored: {log_id}")
        return log_entry

    # ============================================================
    # SCORING UTILITIES
    # ============================================================

    @staticmethod
    def calculate_risk_level(score: int) -> str:
        """
        Calculate risk level based on credit score.

        Args:
            score: Credit score (300-850)

        Returns:
            str: 'Low', 'Medium', or 'High'
        """
        if score >= 700:
            return CreditCheckLog.RiskLevel.LOW
        elif score >= 500:
            return CreditCheckLog.RiskLevel.MEDIUM
        else:
            return CreditCheckLog.RiskLevel.HIGH

    @staticmethod
    def is_score_passing(score: int, threshold: int = 600) -> bool:
        """
        Check if a credit score passes the minimum threshold.

        Args:
            score: Credit score (300-850)
            threshold: Minimum passing score (default: 600)

        Returns:
            bool: True if score passes
        """
        return score >= threshold

    @staticmethod
    def get_score_range() -> Dict[str, int]:
        """
        Get the valid range for credit scores.

        Returns:
            dict: {'min': 300, 'max': 850}
        """
        return {'min': 300, 'max': 850}

    @staticmethod
    def validate_score(score: int) -> bool:
        """
        Validate a credit score is within the valid range.

        Args:
            score: Credit score to validate

        Returns:
            bool: True if valid
        """
        return 300 <= score <= 850