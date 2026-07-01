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
    def delete(agreement_id, user=None, request=None, allow_delete_signed=False):
        """
        Soft delete a loan agreement.
        
        Args:
            agreement_id: ID of the agreement to delete
            user: User performing the action
            request: HTTP request object
            allow_delete_signed: Whether to allow deletion of signed agreements
        """
        agreement = LoanAgreementService.get_by_id(agreement_id)
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        # Check if signed and deletion is allowed
        if agreement.status == LoanAgreement.Status.SIGNED and not allow_delete_signed:
            raise ValidationError({
                'detail': 'Cannot delete a signed agreement. Admin override required.'
            })
        
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
    
    # ============================================================
    # RESTORE
    # ============================================================

    @staticmethod
    @transaction.atomic
    def restore(agreement_id, user=None, request=None):
        """
        Restore a soft-deleted loan agreement.
        
        Args:
            agreement_id: ID of the agreement to restore
            user: User performing the action
            request: HTTP request object
        
        Returns:
            LoanAgreement: The restored agreement instance
        """
        agreement = LoanAgreement.objects.filter(id=agreement_id).first()
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        if not agreement.deleted_at:
            raise ValidationError({'id': 'Loan agreement is not deleted.'})
        
        agreement.restore()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_restore',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'restored_at': timezone.now()}
            )
        
        logger.info(f"Loan agreement restored: {agreement.id}")
        return agreement


    # ============================================================
    # PERMANENT DELETE
    # ============================================================

    @staticmethod
    @transaction.atomic
    def permanent_delete(agreement_id, user=None, request=None, allow_delete_signed=False):
        """
        Permanently delete a loan agreement (hard delete).
        
        Args:
            agreement_id: ID of the agreement to permanently delete
            user: User performing the action
            request: HTTP request object
            allow_delete_signed: Whether to allow deletion of signed agreements
        """
        agreement = LoanAgreement.objects.filter(id=agreement_id).first()
        if not agreement:
            raise ValidationError({'id': 'Loan agreement not found.'})
        
        # Check if signed and deletion is allowed
        if agreement.status == LoanAgreement.Status.SIGNED and not allow_delete_signed:
            raise ValidationError({
                'detail': 'Cannot permanently delete a signed agreement. Admin override required.'
            })
        
        # Audit log before deletion
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_permanent_delete',
                model_name='LoanAgreement',
                object_id=str(agreement.id),
                changes={'permanent': True}
            )
        
        # Delete file if exists
        if agreement.file:
            try:
                agreement.file.delete(save=False)
            except Exception as e:
                logger.warning(f"Failed to delete file for agreement {agreement.id}: {e}")
        
        agreement.delete()
        logger.info(f"Loan agreement permanently deleted: {agreement_id}")


    # ============================================================
    # BULK OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def bulk_create(agreements_data, user=None, request=None):
        """
        Bulk create multiple loan agreements.
        
        Args:
            agreements_data: List of agreement data dictionaries
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'created': list of created agreements, 'errors': list of errors}
        """
        results = {'created': [], 'errors': []}
        
        for data in agreements_data:
            try:
                # Validate required fields
                if not data.get('debt_id'):
                    raise ValidationError({'debt_id': 'Debt ID is required.'})
                
                agreement = LoanAgreementService.create(data, user, request)
                results['created'].append(agreement)
            except Exception as e:
                results['errors'].append({
                    'agreement': data,
                    'error': str(e)
                })
        
        return results


    @staticmethod
    @transaction.atomic
    def bulk_update(updates, user=None, request=None):
        """
        Bulk update multiple loan agreements.
        
        Args:
            updates: List of dicts with 'id' and 'updates' keys
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'updated': list of updated agreements, 'errors': list of errors}
        """
        results = {'updated': [], 'errors': []}
        
        for item in updates:
            try:
                agreement_id = item.get('id')
                data = item.get('updates', {})
                
                if not agreement_id:
                    raise ValidationError({'id': 'Agreement ID is required.'})
                
                updated = LoanAgreementService.update(agreement_id, data, user, request)
                results['updated'].append(updated)
            except Exception as e:
                results['errors'].append({
                    'id': item.get('id'),
                    'updates': item.get('updates', {}),
                    'error': str(e)
                })
        
        return results


    # ============================================================
    # STATISTICS
    # ============================================================

    @staticmethod
    def get_statistics():
        """
        Get loan agreement statistics.
        
        Returns:
            dict: Statistics including totals and breakdowns
        """
        qs = LoanAgreement.objects.filter(deleted_at__isnull=True)
        
        total_agreements = qs.count()
        draft_count = qs.filter(status=LoanAgreement.Status.DRAFT).count()
        signed_count = qs.filter(status=LoanAgreement.Status.SIGNED).count()
        
        # With files
        with_files = qs.filter(file__isnull=False).count()
        
        # Unique lenders
        unique_lenders = qs.exclude(lender_name__isnull=True).exclude(lender_name='').values('lender_name').distinct().count()
        
        # Average agreements per debt
        from django.db.models import Count
        debt_agreement_counts = qs.values('debt').annotate(count=Count('id'))
        total_debts_with_agreements = debt_agreement_counts.count()
        total_agreements = sum(item['count'] for item in debt_agreement_counts)
        avg_per_debt = total_agreements / total_debts_with_agreements if total_debts_with_agreements > 0 else 0
        
        return {
            'total_agreements': total_agreements,
            'draft_count': draft_count,
            'signed_count': signed_count,
            'with_files': with_files,
            'unique_lenders': unique_lenders,
            'average_agreements_per_debt': round(avg_per_debt, 2),
        }


    # ============================================================
    # EXPORT
    # ============================================================

    @staticmethod
    def export_agreements(filters=None):
        """
        Export loan agreements data for reporting.
        
        Args:
            filters: Optional filters
        
        Returns:
            list: List of agreement dictionaries with selected fields
        """
        qs = LoanAgreement.objects.filter(deleted_at__isnull=True).select_related('debt', 'debt__borrower')
        
        if filters:
            if filters.get('debt_id'):
                qs = qs.filter(debt_id=filters['debt_id'])
            if filters.get('status'):
                qs = qs.filter(status=filters['status'])
            if filters.get('borrower_id'):
                qs = qs.filter(debt__borrower_id=filters['borrower_id'])
            if filters.get('lender_name'):
                qs = qs.filter(lender_name__icontains=filters['lender_name'])
        
        export_data = []
        for agreement in qs:
            export_data.append({
                'id': agreement.id,
                'debt_id': agreement.debt_id,
                'debt_name': agreement.debt.name if agreement.debt else None,
                'borrower_name': agreement.debt.borrower.name if agreement.debt and agreement.debt.borrower else None,
                'status': agreement.status,
                'agreement_date': agreement.agreement_date.isoformat() if agreement.agreement_date else None,
                'lender_name': agreement.lender_name,
                'signed_at': agreement.signed_at.isoformat() if agreement.signed_at else None,
                'signed_by': agreement.signed_by,
                'principal_amount': float(agreement.principal_amount) if agreement.principal_amount else None,
                'interest_rate': float(agreement.interest_rate) if agreement.interest_rate else None,
                'penalty_rate': float(agreement.penalty_rate) if agreement.penalty_rate else None,
                'due_date': agreement.due_date.isoformat() if agreement.due_date else None,
                'has_file': agreement.has_file,
                'created_at': agreement.created_at.isoformat(),
            })
        
        return export_data


    # ============================================================
    # IMPORT FROM CSV
    # ============================================================

    @staticmethod
    @transaction.atomic
    def import_from_csv(file_path, user=None, request=None):
        """
        Import loan agreements from CSV file.
        
        Args:
            file_path: Path to CSV file
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'imported': list of imported agreements, 'errors': list of errors}
        """
        import csv
        from io import StringIO
        
        results = {'imported': [], 'errors': []}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            reader = csv.DictReader(StringIO(content))
            row_number = 1
            
            for row in reader:
                row_number += 1
                try:
                    # Find debt
                    debt_id = row.get('debt_id')
                    if debt_id:
                        debt = Debt.objects.filter(id=debt_id, deleted_at__isnull=True).first()
                    else:
                        debt_name = row.get('debt_name')
                        if debt_name:
                            debt = Debt.objects.filter(name__icontains=debt_name, deleted_at__isnull=True).first()
                        else:
                            debt = None
                    
                    if not debt:
                        raise ValidationError({'debt': f'Debt not found. debt_id: {debt_id}, debt_name: {row.get("debt_name")}'})
                    
                    # Prepare agreement data
                    agreement_data = {
                        'debt_id': debt.id,
                        'status': row.get('status', LoanAgreement.Status.DRAFT),
                        'agreement_date': row.get('agreement_date'),
                        'lender_name': row.get('lender_name'),
                        'terms_text': row.get('terms_text'),
                        'principal_amount': row.get('principal_amount'),
                        'interest_rate': row.get('interest_rate'),
                        'penalty_rate': row.get('penalty_rate'),
                        'due_date': row.get('due_date'),
                        'purpose': row.get('purpose'),
                    }
                    
                    agreement = LoanAgreementService.create(agreement_data, user, request)
                    results['imported'].append(agreement)
                    
                except Exception as e:
                    results['errors'].append({
                        'row': row_number,
                        'data': row,
                        'error': str(e)
                    })
            
            return results
        
        except Exception as e:
            raise ValidationError({'file': f'Failed to read CSV: {str(e)}'})