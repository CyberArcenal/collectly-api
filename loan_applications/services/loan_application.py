import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Q, Avg, Count, Sum
from audit.utils.log import log_audit_event
from loan_applications.models.loan_application import LoanApplication
from borrowers.models.borrower import Borrower
from borrowers.services.borrower import BorrowerService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class LoanApplicationService:
    """
    Service layer for LoanApplication CRUD operations.
    """

    @staticmethod
    def get_by_id(application_id, include_deleted=False):
        """
        Get a single loan application by ID.
        """
        qs = LoanApplication.objects.select_related('debtor')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=application_id)
        except LoanApplication.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='created_at', sort_order='desc'):
        """
        Get paginated list of loan applications with filters.
        """
        qs = LoanApplication.objects.filter(deleted_at__isnull=True)
        
        if filters:
            if filters.get('status'):
                qs = qs.filter(status=filters['status'])
            if filters.get('debtor_id'):
                qs = qs.filter(debtor_id=filters['debtor_id'])
            if filters.get('from_date'):
                qs = qs.filter(created_at__gte=filters['from_date'])
            if filters.get('to_date'):
                qs = qs.filter(created_at__lte=filters['to_date'])
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(debtor_name__icontains=search) |
                    Q(purpose__icontains=search) |
                    Q(debtor_email__icontains=search)
                )
            if filters.get('include_deleted'):
                qs = LoanApplication.objects.all()
        
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
        Create a new loan application.
        """
        debtor_id = data.get('debtor_id')
        debtor_name = data.get('debtor_name')
        
        # If debtor_id is provided, validate it exists
        if debtor_id:
            debtor = BorrowerService.get_by_id(debtor_id)
            if not debtor:
                raise ValidationError({'debtor_id': 'Borrower not found.'})
            debtor_name = debtor.name
        
        # If new debtor data is provided, create debtor first
        if data.get('new_debtor'):
            new_debtor_data = data['new_debtor']
            debtor = BorrowerService.create(
                data=new_debtor_data,
                user=user,
                request=request
            )
            debtor_id = debtor.id
            debtor_name = debtor.name
        
        # Validate required fields
        if not debtor_name:
            raise ValidationError({'debtor_name': 'Debtor name is required.'})
        
        application = LoanApplication.objects.create(
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            debtor_contact=data.get('debtor_contact'),
            debtor_email=data.get('debtor_email'),
            debtor_address=data.get('debtor_address'),
            requested_amount=data['requested_amount'],
            purpose=data['purpose'],
            proposed_due_date=data['proposed_due_date'],
            interest_rate=data.get('interest_rate'),
            status=LoanApplication.Status.PENDING
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_create',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'data': data}
            )
        
        logger.info(f"Loan application created: {application.id} - {application.debtor_name}")
        return application

    @staticmethod
    @transaction.atomic
    def update(application_id, data, user=None, request=None):
        """
        Update a loan application (only if pending).
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})
        
        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({'id': f'Cannot update application with status {application.status}.'})
        
        # Update fields
        update_fields = ['debtor_name', 'debtor_contact', 'debtor_email', 'debtor_address',
                        'requested_amount', 'purpose', 'proposed_due_date', 'interest_rate']
        for field in update_fields:
            if field in data:
                setattr(application, field, data[field])
        
        application.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_update',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'data': data}
            )
        
        logger.info(f"Loan application updated: {application.id}")
        return application

    @staticmethod
    @transaction.atomic
    def approve(application_id, user=None, request=None):
        """
        Approve a loan application.
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})
        
        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({'id': f'Cannot approve application with status {application.status}.'})
        
        # If no debtor, create one
        if not application.debtor_id:
            debtor = BorrowerService.create(
                data={
                    'name': application.debtor_name,
                    'contact': application.debtor_contact,
                    'email': application.debtor_email,
                    'address': application.debtor_address,
                },
                user=user,
                request=request
            )
            application.debtor = debtor
        
        # Approve
        application.status = LoanApplication.Status.APPROVED
        application.approved_at = timezone.now()
        application.approved_by = user.username if user else 'system'
        application.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_approved',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'approved_by': user.username}
            )
        
        logger.info(f"Loan application approved: {application.id}")
        return application

    @staticmethod
    @transaction.atomic
    def reject(application_id, reason=None, user=None, request=None):
        """
        Reject a loan application.
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})
        
        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({'id': f'Cannot reject application with status {application.status}.'})
        
        application.status = LoanApplication.Status.REJECTED
        application.rejected_at = timezone.now()
        application.rejection_reason = reason
        application.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_rejected',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'reason': reason}
            )
        
        logger.info(f"Loan application rejected: {application.id}")
        return application

    @staticmethod
    @transaction.atomic
    def delete(application_id, user=None, request=None):
        """
        Soft delete a loan application.
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})
        
        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({'id': f'Cannot delete application with status {application.status}.'})
        
        application.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_delete',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'deleted_at': application.deleted_at}
            )
        
        logger.info(f"Loan application soft-deleted: {application.id}")
        return application

    @staticmethod
    def get_statistics():
        """
        Get loan application statistics.
        """
        qs = LoanApplication.objects.filter(deleted_at__isnull=True)
        
        total = qs.count()
        pending = qs.filter(status=LoanApplication.Status.PENDING).count()
        approved = qs.filter(status=LoanApplication.Status.APPROVED).count()
        rejected = qs.filter(status=LoanApplication.Status.REJECTED).count()
        
        # Total requested amount
        total_amount = qs.aggregate(total=Sum('requested_amount'))['total'] or 0
        
        # Last 30 days
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recent = qs.filter(created_at__gte=thirty_days_ago).count()
        
        return {
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'total_requested_amount': total_amount,
            'applications_last_30_days': recent,
        }