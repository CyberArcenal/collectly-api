import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Count, Sum, Avg, Min, Max
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from loan_applications.models.loan_application import LoanApplication
from borrowers.models.borrower import Borrower
from borrowers.services.borrower import BorrowerService
from system_settings.utils import (
    max_loan_amount,
    min_loan_amount,
    default_interest_rate,
    enforce_credit_check,
    credit_check_validity_days,
    min_credit_score_for_approval,
    require_loan_agreement,
)
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class LoanApplicationService:
    """
    Service layer for LoanApplication CRUD operations.

    Handles creation, updates, approval, rejection, and deletion of loan applications.
    Also manages credit check enforcement and debtor creation during approval.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(application_id: int, include_deleted: bool = False) -> Optional[LoanApplication]:
        """
        Get a single loan application by ID.

        Args:
            application_id: ID of the application to retrieve
            include_deleted: Whether to include soft-deleted applications

        Returns:
            LoanApplication instance or None if not found
        """
        qs = LoanApplication.objects.select_related('debtor')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        try:
            return qs.get(id=application_id)
        except LoanApplication.DoesNotExist:
            return None

    @staticmethod
    def get_by_debtor(debtor_id: int, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """
        Get paginated loan applications for a specific debtor.

        Args:
            debtor_id: ID of the debtor
            page: Page number for pagination
            limit: Number of items per page

        Returns:
            dict: Paginated list of applications
        """
        qs = LoanApplication.objects.filter(
            debtor_id=debtor_id,
            deleted_at__isnull=True
        ).order_by('-created_at')

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_list(
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = 'created_at',
        sort_order: str = 'desc'
    ) -> Dict[str, Any]:
        """
        Get paginated list of loan applications with filters.

        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'

        Returns:
            dict: {
                'data': list of LoanApplication objects,
                'pagination': pagination metadata
            }
        """
        qs = LoanApplication.objects.select_related('debtor')

        # Handle deleted filtering based on include_deleted flag
        include_deleted = filters.get('include_deleted', False) if filters else False
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        # Apply filters
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

            if filters.get('min_amount'):
                qs = qs.filter(requested_amount__gte=filters['min_amount'])

            if filters.get('max_amount'):
                qs = qs.filter(requested_amount__lte=filters['max_amount'])

        # Apply sorting
        if sort_order.lower() == 'asc':
            sort_by = sort_by
        else:
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_statistics() -> Dict[str, Any]:
        """
        Get comprehensive loan application statistics.

        Returns:
            dict: Statistics including counts by status and total requested amounts
        """
        qs = LoanApplication.objects.filter(deleted_at__isnull=True)

        total = qs.count()
        status_counts = qs.values('status').annotate(count=Count('id'))

        # Build status counts dictionary
        status_stats = {}
        for item in status_counts:
            status_stats[item['status']] = item['count']

        # Total requested amount
        total_amount = qs.aggregate(total=Sum('requested_amount'))['total'] or Decimal('0')

        # Average requested amount
        avg_amount = qs.aggregate(avg=Avg('requested_amount'))['avg'] or Decimal('0')

        # Last 30 days
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent = qs.filter(created_at__gte=thirty_days_ago).count()

        # Amount range stats
        amount_stats = qs.aggregate(
            min_amount=Min('requested_amount'),
            max_amount=Max('requested_amount'),
        )

        return {
            'total': total,
            'pending': status_stats.get(LoanApplication.Status.PENDING, 0),
            'approved': status_stats.get(LoanApplication.Status.APPROVED, 0),
            'rejected': status_stats.get(LoanApplication.Status.REJECTED, 0),
            'total_requested_amount': total_amount,
            'average_requested_amount': round(avg_amount, 2),
            'min_requested_amount': amount_stats.get('min_amount') or Decimal('0'),
            'max_requested_amount': amount_stats.get('max_amount') or Decimal('0'),
            'applications_last_30_days': recent,
        }

    @staticmethod
    def get_approval_rate() -> Dict[str, Any]:
        """
        Get approval rate statistics for loan applications.

        Returns:
            dict: Approval rate and related metrics
        """
        qs = LoanApplication.objects.filter(deleted_at__isnull=True)

        total = qs.count()
        approved = qs.filter(status=LoanApplication.Status.APPROVED).count()
        rejected = qs.filter(status=LoanApplication.Status.REJECTED).count()
        pending = qs.filter(status=LoanApplication.Status.PENDING).count()

        # Calculate rates
        approval_rate = (approved / total * 100) if total > 0 else 0
        rejection_rate = (rejected / total * 100) if total > 0 else 0

        # Average processing time for approved applications
        approved_apps = qs.filter(
            status=LoanApplication.Status.APPROVED,
            approved_at__isnull=False
        )

        processing_times = []
        for app in approved_apps:
            processing_time = (app.approved_at - app.created_at).total_seconds() / 3600  # hours
            processing_times.append(processing_time)

        avg_processing_time = sum(processing_times) / len(processing_times) if processing_times else 0

        return {
            'total_applications': total,
            'approved_count': approved,
            'rejected_count': rejected,
            'pending_count': pending,
            'approval_rate': round(approval_rate, 2),
            'rejection_rate': round(rejection_rate, 2),
            'average_processing_hours': round(avg_processing_time, 2),
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user=None, request=None) -> LoanApplication:
        """
        Create a new loan application.

        This method:
        1. Validates debtor exists or creates new debtor
        2. Creates the application with snapshot data
        3. Sets status to PENDING

        Args:
            data: Dictionary containing application data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The created application instance

        Raises:
            ValidationError: If validation fails
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

        requested_amount = Decimal(str(data.get('requested_amount', 0)))
        if requested_amount <= 0:
            raise ValidationError({'requested_amount': 'Requested amount must be greater than zero.'})

        # Validate proposed due date is not in the past
        proposed_due_date = data.get('proposed_due_date')
        if proposed_due_date and isinstance(proposed_due_date, str):
            from datetime import datetime
            proposed_due_date = datetime.fromisoformat(proposed_due_date).date()

        if proposed_due_date and proposed_due_date < timezone.now().date():
            raise ValidationError({
                'proposed_due_date': 'Due date cannot be in the past.'
            })

        # Create application
        application = LoanApplication.objects.create(
            debtor_id=debtor_id,
            debtor_name=debtor_name,
            debtor_contact=data.get('debtor_contact'),
            debtor_email=data.get('debtor_email'),
            debtor_address=data.get('debtor_address'),
            requested_amount=requested_amount,
            purpose=data['purpose'],
            proposed_due_date=proposed_due_date,
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
    def update(application_id: int, data: Dict[str, Any], user=None, request=None) -> LoanApplication:
        """
        Update a loan application (only if pending).

        Args:
            application_id: ID of the application to update
            data: Dictionary containing updated fields
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The updated application instance

        Raises:
            ValidationError: If validation fails or application not pending
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({
                'id': f'Cannot update application with status {application.status}.'
            })

        # Validate requested amount if provided
        if data.get('requested_amount'):
            requested_amount = Decimal(str(data['requested_amount']))
            if requested_amount <= 0:
                raise ValidationError({'requested_amount': 'Requested amount must be greater than zero.'})

        # Validate proposed due date if provided
        if data.get('proposed_due_date'):
            proposed_due_date = data['proposed_due_date']
            if isinstance(proposed_due_date, str):
                from datetime import datetime
                proposed_due_date = datetime.fromisoformat(proposed_due_date).date()

            if proposed_due_date < timezone.now().date():
                raise ValidationError({
                    'proposed_due_date': 'Due date cannot be in the past.'
                })

        # Update fields
        update_fields = [
            'debtor_name', 'debtor_contact', 'debtor_email', 'debtor_address',
            'requested_amount', 'purpose', 'proposed_due_date', 'interest_rate'
        ]
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
    def approve(application_id: int, user=None, request=None) -> LoanApplication:
        """
        Approve a loan application.

        This method:
        1. Validates the application is pending
        2. Checks amount against system limits
        3. Enforces credit check if enabled
        4. Creates debtor if not exists
        5. Updates application status to APPROVED

        Args:
            application_id: ID of the application to approve
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The approved application instance

        Raises:
            ValidationError: If validation fails
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({
                'id': f'Cannot approve application with status {application.status}.'
            })

        # --- Amount validation using system settings ---
        max_amount = max_loan_amount()
        min_amount = min_loan_amount()

        if max_amount > 0 and application.requested_amount > max_amount:
            raise ValidationError({
                'requested_amount': f'Requested amount (₱{application.requested_amount:,.2f}) exceeds maximum loan amount (₱{max_amount:,.2f}).'
            })

        if min_amount > 0 and application.requested_amount < min_amount:
            raise ValidationError({
                'requested_amount': f'Requested amount (₱{application.requested_amount:,.2f}) is below minimum loan amount (₱{min_amount:,.2f}).'
            })

        # --- Interest rate validation ---
        interest_rate = application.interest_rate
        if interest_rate is None:
            interest_rate = default_interest_rate()
            application.interest_rate = interest_rate

        # --- Credit check enforcement ---
        need_credit_check = enforce_credit_check()
        if need_credit_check:
            # Get the latest credit check for this debtor
            from borrowers.services.credit_check import CreditCheckService

            if not application.debtor_id:
                raise ValidationError({
                    'debtor': 'No debtor associated with this application. Please create debtor first.'
                })

            latest_check = CreditCheckService.get_latest(application.debtor_id)

            if not latest_check:
                raise ValidationError({
                    'credit_check': f'Credit check required before approval. No credit check found for debtor ID {application.debtor_id}.'
                })

            validity_days = credit_check_validity_days()
            check_date = latest_check.date_checked.date() if latest_check.date_checked else None

            if check_date:
                days_since_check = (timezone.now().date() - check_date).days
                if days_since_check > validity_days:
                    raise ValidationError({
                        'credit_check': f'Credit check is too old ({days_since_check} days). Please perform a new credit check (validity: {validity_days} days).'
                    })

            min_score = min_credit_score_for_approval()
            if min_score > 0 and latest_check.score < min_score:
                raise ValidationError({
                    'credit_check': f'Credit score ({latest_check.score}) is below the minimum required ({min_score}). Approval denied.'
                })

        # --- Loan agreement requirement (soft check) ---
        need_agreement = require_loan_agreement()
        if need_agreement:
            # Check if agreement exists (will be handled by separate flow)
            logger.info(f"Loan agreement required for application {application_id}")

        # --- If no debtor, create one ---
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

        # --- Approve ---
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
                changes={
                    'approved_by': user.username,
                    'interest_rate': float(application.interest_rate) if application.interest_rate else None,
                }
            )

        logger.info(f"Loan application approved: {application.id}")
        return application

    @staticmethod
    @transaction.atomic
    def reject(application_id: int, reason: Optional[str] = None, user=None, request=None) -> LoanApplication:
        """
        Reject a loan application.

        Args:
            application_id: ID of the application to reject
            reason: Reason for rejection
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The rejected application instance

        Raises:
            ValidationError: If validation fails
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({
                'id': f'Cannot reject application with status {application.status}.'
            })

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
    def delete(application_id: int, user=None, request=None) -> LoanApplication:
        """
        Soft delete a loan application (only if pending).

        Args:
            application_id: ID of the application to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The soft-deleted application instance

        Raises:
            ValidationError: If validation fails or application not pending
        """
        application = LoanApplicationService.get_by_id(application_id)
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({
                'id': f'Cannot delete application with status {application.status}.'
            })

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
    @transaction.atomic
    def restore(application_id: int, user=None, request=None) -> LoanApplication:
        """
        Restore a soft-deleted loan application.

        Args:
            application_id: ID of the application to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            LoanApplication: The restored application instance

        Raises:
            ValidationError: If application not found or not deleted
        """
        application = LoanApplication.objects.filter(id=application_id).first()
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if not application.deleted_at:
            raise ValidationError({'id': 'Loan application is not deleted.'})

        application.restore()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_restore',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'restored_at': timezone.now()}
            )

        logger.info(f"Loan application restored: {application.id}")
        return application

    @staticmethod
    @transaction.atomic
    def permanent_delete(application_id: int, user=None, request=None) -> None:
        """
        Permanently delete a loan application (hard delete).

        Args:
            application_id: ID of the application to permanently delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Raises:
            ValidationError: If application not found or not pending
        """
        application = LoanApplication.objects.filter(id=application_id).first()
        if not application:
            raise ValidationError({'id': 'Loan application not found.'})

        if application.status != LoanApplication.Status.PENDING:
            raise ValidationError({
                'id': f'Cannot permanently delete application with status {application.status}.'
            })

        # Audit log before deletion
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_application_permanent_delete',
                model_name='LoanApplication',
                object_id=str(application.id),
                changes={'permanent': True}
            )

        application.delete()

        logger.info(f"Loan application permanently deleted: {application_id}")

    # ============================================================
    # UTILITY METHODS
    # ============================================================

    @staticmethod
    def get_application_summary(debtor_id: int) -> Dict[str, Any]:
        """
        Get application summary for a specific debtor.

        Args:
            debtor_id: ID of the debtor

        Returns:
            dict: Summary of applications for the debtor
        """
        qs = LoanApplication.objects.filter(
            debtor_id=debtor_id,
            deleted_at__isnull=True
        )

        total = qs.count()
        pending = qs.filter(status=LoanApplication.Status.PENDING).count()
        approved = qs.filter(status=LoanApplication.Status.APPROVED).count()
        rejected = qs.filter(status=LoanApplication.Status.REJECTED).count()

        total_amount = qs.aggregate(total=Sum('requested_amount'))['total'] or Decimal('0')

        # Last application
        last_application = qs.order_by('-created_at').first()

        return {
            'debtor_id': debtor_id,
            'total_applications': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'total_requested_amount': total_amount,
            'last_application_date': last_application.created_at if last_application else None,
            'last_application_status': last_application.status if last_application else None,
        }

    @staticmethod
    def can_apply(debtor_id: int) -> Dict[str, Any]:
        """
        Check if a debtor can submit a new application.

        Args:
            debtor_id: ID of the debtor

        Returns:
            dict: {
                'can_apply': bool,
                'reason': str or None,
                'pending_count': int
            }
        """
        # Check for pending applications
        pending_count = LoanApplication.objects.filter(
            debtor_id=debtor_id,
            status=LoanApplication.Status.PENDING,
            deleted_at__isnull=True
        ).count()

        if pending_count > 0:
            return {
                'can_apply': False,
                'reason': f'You have {pending_count} pending application(s). Please wait for approval.',
                'pending_count': pending_count,
            }

        # Check for recent rejected applications (optional guardrail)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_rejected = LoanApplication.objects.filter(
            debtor_id=debtor_id,
            status=LoanApplication.Status.REJECTED,
            rejected_at__gte=thirty_days_ago,
            deleted_at__isnull=True
        ).count()

        if recent_rejected > 0:
            return {
                'can_apply': True,
                'reason': 'You have recent rejected applications, but you may still apply.',
                'pending_count': 0,
            }

        return {
            'can_apply': True,
            'reason': None,
            'pending_count': 0,
        }