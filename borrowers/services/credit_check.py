import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Q, Avg, Count
from audit.utils.log import log_audit_event
from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.models.borrower import Borrower
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class CreditCheckService:
    """
    Service layer for CreditCheckLog operations.
    """

    @staticmethod
    def get_by_id(log_id):
        """
        Get a single credit check log by ID.
        """
        try:
            return CreditCheckLog.objects.get(id=log_id)
        except CreditCheckLog.DoesNotExist:
            return None

    @staticmethod
    def get_by_borrower(borrower_id, page=1, limit=20):
        """
        Get paginated credit check history for a borrower.
        """
        qs = CreditCheckLog.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True
        ).order_by('-date_checked')
        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_latest(borrower_id):
        """
        Get the most recent credit check for a borrower.
        """
        return CreditCheckLog.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True
        ).order_by('-date_checked').first()

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new credit check log.
        """
        debtor = Borrower.objects.filter(id=data['debtor_id']).first()
        if not debtor:
            raise ValidationError({'debtor_id': 'Borrower not found.'})
        
        # Auto-calculate risk level based on score
        score = data.get('score', 0)
        risk_level = data.get('risk_level')
        if not risk_level:
            if score >= 700:
                risk_level = CreditCheckLog.RiskLevel.LOW
            elif score >= 500:
                risk_level = CreditCheckLog.RiskLevel.MEDIUM
            else:
                risk_level = CreditCheckLog.RiskLevel.HIGH
        
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
                    'risk_level': risk_level
                }
            )
        
        logger.info(f"Credit check performed for borrower {debtor.id}: score={score}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def delete(log_id, user=None, request=None):
        """
        Soft delete a credit check log.
        """
        log_entry = CreditCheckService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Credit check log not found.'})
        
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
    def get_statistics(borrower_id=None):
        """
        Get credit check statistics.
        """
        qs = CreditCheckLog.objects.filter(deleted_at__isnull=True)
        if borrower_id:
            qs = qs.filter(debtor_id=borrower_id)
        
        total = qs.count()
        
        # Average score
        avg_score = qs.aggregate(avg=Avg('score'))['avg'] or 0
        
        # Risk level distribution
        risk_distribution = qs.values('risk_level').annotate(
            count=Count('id')
        ).order_by('risk_level')
        
        # Excellent scores (>= 750)
        excellent = qs.filter(score__gte=750).count()
        
        # Passing scores (>= 600)
        passing = qs.filter(score__gte=600).count()
        
        return {
            'total': total,
            'average_score': round(avg_score, 2),
            'excellent_count': excellent,
            'passing_count': passing,
            'risk_distribution': list(risk_distribution),
        }