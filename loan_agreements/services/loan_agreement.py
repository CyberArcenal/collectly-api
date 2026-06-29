import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from loan_agreements.models.loan_agreement import LoanAgreement
from debts.models.debt import Debt
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class LoanAgreementService:
    """
    Service layer for LoanAgreement CRUD operations.
    """

    @staticmethod
    def get_by_id(agreement_id, include_deleted=False):
        """
        Get a single loan agreement by ID.
        """
        qs = LoanAgreement.objects.select_related('debt')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=agreement_id)
        except LoanAgreement.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='created_at', sort_order='desc'):
        """
        Get paginated list of loan agreements with filters.
        """
        qs = LoanAgreement.objects.select_related('debt').filter(deleted_at__isnull=True)
        
        if filters:
            if filters.get('debt_id'):
                qs = qs.filter(debt_id=filters['debt_id'])
            if filters.get('status'):
                qs = qs.filter(status=filters['status'])
            if filters.get('borrower_id'):
                qs = qs.filter(debt__borrower_id=filters['borrower_id'])
            if filters.get('lender_name'):
                qs = qs.filter(lender_name__icontains=filters['lender_name'])
            if filters.get('agreement_date_from'):
                qs = qs.filter(agreement_date__gte=filters['agreement_date_from'])
            if filters.get('agreement_date_to'):
                qs = qs.filter(agreement_date__lte=filters['agreement_date_to'])
            if filters.get('has_file') is not None:
                if filters['has_file']:
                    qs = qs.filter(file__isnull=False)
                else:
                    qs = qs.filter(file__isnull=True)
            if filters.get('include_deleted'):
                qs = LoanAgreement.objects.select_related('debt')
        
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
        Create a new loan agreement.
        """
        debt = Debt.objects.filter(id=data['debt_id']).first()
        if not debt:
            raise ValidationError({'debt_id': 'Debt not found.'})
        
        # Check if there's already a signed agreement
        if LoanAgreement.objects.filter(debt=debt, status=LoanAgreement.Status.SIGNED).exists():
            raise ValidationError({'debt_id': 'Debt already has a signed agreement.'})
        
        agreement = LoanAgreement.objects.create(
            debt=debt,
            status=data.get('status', LoanAgreement.Status.DRAFT),
            agreement_date=data.get('agreement_date'),
            lender_name=data.get('lender_name'),
            terms_text=data.get('terms_text'),
            file=data.get('file'),
            
            # Snapshot fields
            principal_amount=data.get('principal_amount'),
            interest_rate=data.get('interest_rate'),
            penalty_rate=data.get('penalty_rate'),
            due_date=data.get('due_date'),
            purpose=data.get('purpose'),
            loan_start_date=data.get('loan_start_date'),
            anniversary_day=data.get('anniversary_day'),
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_create',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'data': data}
            )
        
        logger.info(f"Loan agreement created: {agreement.id}")
        return agreement

    @staticmethod
    @transaction.atomic
    def update(agreement_id, data, user=None, request=None):
        """
        Update a loan agreement (only if draft).
        """
        agreement = LoanAgreementService.get_by_id(agreement_id)
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        if agreement.status == LoanAgreement.Status.SIGNED:
            raise ValidationError({'id': 'Cannot update a signed agreement.'})
        
        # Update fields
        update_fields = ['status', 'agreement_date', 'lender_name', 'terms_text', 'file',
                        'principal_amount', 'interest_rate', 'penalty_rate', 'due_date',
                        'purpose', 'loan_start_date', 'anniversary_day']
        for field in update_fields:
            if field in data:
                setattr(agreement, field, data[field])
        
        agreement.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_update',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'data': data}
            )
        
        logger.info(f"Loan agreement updated: {agreement.id}")
        return agreement

    @staticmethod
    @transaction.atomic
    def sign(agreement_id, signed_by, user=None, request=None):
        """
        Sign a loan agreement (draft → signed).
        """
        agreement = LoanAgreementService.get_by_id(agreement_id)
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        if agreement.status == LoanAgreement.Status.SIGNED:
            raise ValidationError({'id': 'Agreement is already signed.'})
        
        # Check if there's already a signed agreement for this debt
        if LoanAgreement.objects.filter(
            debt=agreement.debt,
            status=LoanAgreement.Status.SIGNED
        ).exists():
            raise ValidationError({'debt_id': 'Debt already has a signed agreement.'})
        
        # Snapshot debt data
        debt = agreement.debt
        agreement.status = LoanAgreement.Status.SIGNED
        agreement.signed_at = timezone.now()
        agreement.signed_by = signed_by
        agreement.principal_amount = debt.total_amount
        agreement.interest_rate = debt.interest_rate
        agreement.penalty_rate = debt.penalty_rate
        agreement.due_date = debt.due_date
        agreement.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_signed',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'signed_by': signed_by}
            )
        
        logger.info(f"Loan agreement signed: {agreement.id} by {signed_by}")
        return agreement

    @staticmethod
    @transaction.atomic
    def delete(agreement_id, user=None, request=None):
        """
        Soft delete a loan agreement.
        """
        agreement = LoanAgreementService.get_by_id(agreement_id)
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        agreement.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_delete',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'deleted_at': agreement.deleted_at}
            )
        
        logger.info(f"Loan agreement soft-deleted: {agreement.id}")
        return agreement