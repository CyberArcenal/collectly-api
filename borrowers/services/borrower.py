import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q, Count, Sum
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from debts.services.debt import DebtService
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class BorrowerService:
    """
    Service layer for Borrower CRUD operations.

    Handles creation, updates, deletion, and retrieval of borrowers.
    Also manages borrower statistics and bulk operations.
    """

    # ============================================================
    # READ OPERATIONS
    # ============================================================

    @staticmethod
    def get_by_id(borrower_id: int, include_deleted: bool = False) -> Optional[Borrower]:
        """
        Get a single borrower by ID.

        Args:
            borrower_id: ID of the borrower to retrieve
            include_deleted: Whether to include soft-deleted borrowers

        Returns:
            Borrower instance or None if not found
        """
        qs = Borrower.objects.all()
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        try:
            return qs.get(id=borrower_id)
        except Borrower.DoesNotExist:
            return None

    @staticmethod
    def get_by_email(email: str) -> Optional[Borrower]:
        """
        Get a borrower by email address.

        Args:
            email: Email address to search for

        Returns:
            Borrower instance or None if not found
        """
        try:
            return Borrower.objects.get(email=email, deleted_at__isnull=True)
        except Borrower.DoesNotExist:
            return None

    @staticmethod
    def get_by_contact(contact: str) -> Optional[Borrower]:
        """
        Get a borrower by contact number.

        Args:
            contact: Contact number to search for

        Returns:
            Borrower instance or None if not found
        """
        try:
            return Borrower.objects.get(contact=contact, deleted_at__isnull=True)
        except Borrower.DoesNotExist:
            return None

    @staticmethod
    def get_list(
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        limit: int = 20,
        sort_by: str = 'name',
        sort_order: str = 'asc'
    ) -> Dict[str, Any]:
        """
        Get paginated list of borrowers with filters.

        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'

        Returns:
            dict: {
                'data': list of Borrower objects,
                'pagination': pagination metadata
            }
        """
        qs = Borrower.objects.filter(deleted_at__isnull=True)

        # Apply filters
        if filters:
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(name__icontains=search) |
                    Q(email__icontains=search) |
                    Q(contact__icontains=search) |
                    Q(address__icontains=search)
                )

            if filters.get('name'):
                qs = qs.filter(name__icontains=filters['name'])

            if filters.get('email'):
                qs = qs.filter(email=filters['email'])

            if filters.get('contact'):
                qs = qs.filter(contact=filters['contact'])

            if filters.get('has_email') is not None:
                if filters['has_email']:
                    qs = qs.filter(email__isnull=False)
                else:
                    qs = qs.filter(email__isnull=True)

            if filters.get('has_contact') is not None:
                if filters['has_contact']:
                    qs = qs.filter(contact__isnull=False)
                else:
                    qs = qs.filter(contact__isnull=True)

            if filters.get('include_deleted'):
                qs = Borrower.objects.all()

            # Filter by active debts count
            if filters.get('min_debts') is not None:
                from django.db.models import Count
                qs = qs.annotate(active_debts=Count('debts', filter=Q(
                    debts__deleted_at__isnull=True,
                    debts__status__in=['active', 'overdue']
                ))).filter(active_debts__gte=filters['min_debts'])

            if filters.get('max_debts') is not None:
                from django.db.models import Count
                if filters.get('min_debts') is None:
                    qs = qs.annotate(active_debts=Count('debts', filter=Q(
                        debts__deleted_at__isnull=True,
                        debts__status__in=['active', 'overdue']
                    )))
                qs = qs.filter(active_debts__lte=filters['max_debts'])

        # Apply sorting
        if sort_order.lower() == 'desc':
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)

        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_statistics() -> Dict[str, Any]:
        """
        Get comprehensive borrower statistics.

        Returns:
            dict: Statistics including totals and counts
        """
        qs = Borrower.objects.filter(deleted_at__isnull=True)

        total = qs.count()
        with_email = qs.filter(email__isnull=False).count()
        with_contact = qs.filter(contact__isnull=False).count()

        # Recently added (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recently_added = qs.filter(created_at__gte=thirty_days_ago).count()

        # With active debts
        from debts.models.debt import Debt
        with_active_debts = Borrower.objects.filter(
            deleted_at__isnull=True,
            debts__deleted_at__isnull=True,
            debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).distinct().count()

        # Total debt across all borrowers
        from django.db.models import Sum
        total_debt = Borrower.objects.filter(
            deleted_at__isnull=True
        ).aggregate(
            total=Sum('debts__remaining_amount', filter=Q(
                debts__deleted_at__isnull=True,
                debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
            ))
        )['total'] or 0

        return {
            'total': total,
            'with_email': with_email,
            'with_contact': with_contact,
            'recently_added': recently_added,
            'with_active_debts': with_active_debts,
            'total_outstanding_debt': total_debt,
        }

    # ============================================================
    # WRITE OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user=None, request=None) -> Borrower:
        """
        Create a new borrower.

        Args:
            data: Dictionary containing borrower data
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Borrower: The created borrower instance

        Raises:
            ValidationError: If validation fails
        """
        # Validate name is required
        if not data.get('name'):
            raise ValidationError({'name': 'Name is required.'})

        # Validate unique email
        if data.get('email'):
            if Borrower.objects.filter(email=data['email']).exists():
                raise ValidationError({'email': 'Email already exists.'})

        # Validate unique contact
        if data.get('contact'):
            if Borrower.objects.filter(contact=data['contact']).exists():
                raise ValidationError({'contact': 'Contact already exists.'})

        # Create borrower
        borrower = Borrower.objects.create(
            name=data['name'],
            contact=data.get('contact'),
            email=data.get('email'),
            address=data.get('address'),
            notes=data.get('notes'),
            user=data.get('user')
        )

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_create',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={'data': data}
            )

        logger.info(f"Borrower created: {borrower.id} - {borrower.name}")
        return borrower

    @staticmethod
    @transaction.atomic
    def update(borrower_id: int, data: Dict[str, Any], user=None, request=None) -> Borrower:
        """
        Update an existing borrower.

        Args:
            borrower_id: ID of the borrower to update
            data: Dictionary containing updated fields
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Borrower: The updated borrower instance

        Raises:
            ValidationError: If validation fails or borrower not found
        """
        borrower = BorrowerService.get_by_id(borrower_id)
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})

        # Check unique email (if changed)
        if data.get('email') and data['email'] != borrower.email:
            if Borrower.objects.filter(email=data['email']).exists():
                raise ValidationError({'email': 'Email already exists.'})

        # Check unique contact (if changed)
        if data.get('contact') and data['contact'] != borrower.contact:
            if Borrower.objects.filter(contact=data['contact']).exists():
                raise ValidationError({'contact': 'Contact already exists.'})

        # Store old data for audit
        old_data = {
            'name': borrower.name,
            'email': borrower.email,
            'contact': borrower.contact,
        }

        # Update fields
        for field in ['name', 'contact', 'email', 'address', 'notes', 'user']:
            if field in data:
                setattr(borrower, field, data[field])

        borrower.save()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_update',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={
                    'before': old_data,
                    'after': {
                        'name': borrower.name,
                        'email': borrower.email,
                        'contact': borrower.contact,
                    }
                }
            )

        logger.info(f"Borrower updated: {borrower.id} - {borrower.name}")
        return borrower

    @staticmethod
    @transaction.atomic
    def delete(borrower_id: int, user=None, request=None) -> Borrower:
        """
        Soft delete a borrower.

        Args:
            borrower_id: ID of the borrower to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Borrower: The soft-deleted borrower instance

        Raises:
            ValidationError: If borrower not found or already deleted
        """
        borrower = BorrowerService.get_by_id(borrower_id)
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})

        if borrower.deleted_at:
            raise ValidationError({'id': 'Borrower is already deleted.'})

        # Check if borrower has active debts
        from debts.models.debt import Debt
        active_debts = Debt.objects.filter(
            borrower=borrower,
            deleted_at__isnull=True,
            remaining_amount__gt=0
        ).exists()

        if active_debts:
            raise ValidationError({
                'id': 'Cannot delete borrower with active debts. Settle all debts first.'
            })

        borrower.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_delete',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={'deleted_at': borrower.deleted_at}
            )

        logger.info(f"Borrower soft-deleted: {borrower.id} - {borrower.name}")
        return borrower

    @staticmethod
    @transaction.atomic
    def restore(borrower_id: int, user=None, request=None) -> Borrower:
        """
        Restore a soft-deleted borrower.

        Args:
            borrower_id: ID of the borrower to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            Borrower: The restored borrower instance

        Raises:
            ValidationError: If borrower not found or not deleted
        """
        borrower = Borrower.objects.filter(id=borrower_id).first()
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})

        if not borrower.deleted_at:
            raise ValidationError({'id': 'Borrower is not deleted.'})

        borrower.restore()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_restore',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={'restored_at': timezone.now()}
            )

        logger.info(f"Borrower restored: {borrower.id} - {borrower.name}")
        return borrower

    @staticmethod
    @transaction.atomic
    def permanent_delete(borrower_id: int, user=None, request=None) -> None:
        """
        Permanently delete a borrower (hard delete).

        Args:
            borrower_id: ID of the borrower to permanently delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Raises:
            ValidationError: If borrower not found or has related records
        """
        borrower = Borrower.objects.filter(id=borrower_id).first()
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})

        # Check for related records
        from debts.models.debt import Debt
        if Debt.objects.filter(borrower=borrower).exists():
            raise ValidationError({
                'id': 'Cannot permanently delete borrower with existing debts.'
            })

        # Audit log before deletion
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_permanent_delete',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={'permanent': True}
            )

        borrower.delete()

        logger.info(f"Borrower permanently deleted: {borrower_id}")

    # ============================================================
    # BULK OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def bulk_create(borrowers_data: List[Dict[str, Any]], user=None, request=None) -> Dict[str, Any]:
        """
        Create multiple borrowers in bulk.

        Args:
            borrowers_data: List of borrower data dictionaries
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            dict: {
                'created': list of created borrowers,
                'errors': list of errors
            }
        """
        results = {'created': [], 'errors': []}

        for data in borrowers_data:
            try:
                borrower = BorrowerService.create(
                    data=data,
                    user=user,
                    request=request
                )
                results['created'].append(borrower)
            except Exception as e:
                results['errors'].append({
                    'data': data,
                    'error': str(e)
                })

        return results

    @staticmethod
    @transaction.atomic
    def bulk_update(updates: List[Dict[str, Any]], user=None, request=None) -> Dict[str, Any]:
        """
        Update multiple borrowers in bulk.

        Args:
            updates: List of dicts with 'id' and 'data' keys
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            dict: {
                'updated': list of updated borrowers,
                'errors': list of errors
            }
        """
        results = {'updated': [], 'errors': []}

        for item in updates:
            try:
                borrower_id = item.get('id')
                data = item.get('data', {})
                if not borrower_id:
                    raise ValidationError({'id': 'Borrower ID is required.'})

                updated = BorrowerService.update(
                    borrower_id=borrower_id,
                    data=data,
                    user=user,
                    request=request
                )
                results['updated'].append(updated)
            except Exception as e:
                results['errors'].append({
                    'id': item.get('id'),
                    'error': str(e)
                })

        return results

    # ============================================================
    # EXPORT OPERATIONS
    # ============================================================

    @staticmethod
    def export_borrowers(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Export borrowers data for reporting.

        Args:
            filters: Optional filters for the export

        Returns:
            list: List of borrower dictionaries with selected fields
        """
        qs = Borrower.objects.filter(deleted_at__isnull=True)

        if filters:
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(name__icontains=search) |
                    Q(email__icontains=search) |
                    Q(contact__icontains=search)
                )

        borrowers = qs.select_related('user').prefetch_related('debts')

        export_data = []
        for borrower in borrowers:
            total_debt = sum(
                d.remaining_amount for d in borrower.debts.filter(
                    deleted_at__isnull=True,
                    status__in=['active', 'overdue']
                )
            )

            export_data.append({
                'id': borrower.id,
                'name': borrower.name,
                'email': borrower.email,
                'contact': borrower.contact,
                'address': borrower.address,
                'created_at': borrower.created_at.isoformat(),
                'total_outstanding_debt': float(total_debt),
                'active_debt_count': borrower.active_debt_count,
            })

        return export_data
    
    @staticmethod
    def get_overdue_debts(page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """Get all overdue debts (status = 'overdue')."""
        from debts.models.debt import Debt
        
        qs = Debt.objects.filter(
            status=Debt.Status.OVERDUE,
            deleted_at__isnull=True
        ).order_by('due_date')
        
        return paginate_queryset(qs, page, limit)
    
    @staticmethod
    def get_by_borrower(borrower_id: int, include_deleted: bool = False) -> List[Debt]:
        """Get all debts for a specific borrower."""
        qs = Debt.objects.filter(borrower_id=borrower_id)
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        return qs.order_by('due_date').all()
    
    @staticmethod
    def exists_for_borrower(borrower_id: int, debt_name: str) -> bool:
        """Check if a debt with given name exists for a borrower."""
        return Debt.objects.filter(
            borrower_id=borrower_id,
            name=debt_name,
            deleted_at__isnull=True
        ).exists()
        
    @staticmethod
    @transaction.atomic
    def import_from_csv(file_path: str, user=None, request=None) -> Dict[str, Any]:
        """Import debts from CSV file."""
        import csv
        from decimal import Decimal
        
        results = {'imported': [], 'errors': []}
        
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Validate and create debt
                    borrower = Borrower.objects.filter(
                        name=row.get('borrower_name'),
                        deleted_at__isnull=True
                    ).first()
                    
                    if not borrower:
                        raise ValidationError(f"Borrower '{row.get('borrower_name')}' not found")
                    
                    debt_data = {
                        'borrower': borrower,
                        'name': row.get('name'),
                        'total_amount': Decimal(row.get('total_amount', 0)),
                        'due_date': datetime.strptime(row.get('due_date'), '%Y-%m-%d').date(),
                        'interest_rate': Decimal(row.get('interest_rate', 0)) if row.get('interest_rate') else None,
                        'penalty_rate': Decimal(row.get('penalty_rate', 0)) if row.get('penalty_rate') else None,
                    }
                    
                    debt = DebtService.create(debt_data, user, request)
                    results['imported'].append(debt)
                    
                except Exception as e:
                    results['errors'].append({
                        'row': row_num,
                        'data': row,
                        'error': str(e)
                    })
        
        return results
    
    
    @staticmethod
    def export_debts(filters: Optional[Dict[str, Any]] = None, format: str = 'json') -> Dict[str, Any]:
        """Export debts to JSON or CSV."""
        from debts.serializers.debt import DebtListSerializer
        
        # Get debts with filters
        qs = Debt.objects.filter(deleted_at__isnull=True)
        if filters:
            # Apply filters similar to get_list
            pass
        
        debts = qs.select_related('borrower').all()
        
        if format == 'csv':
            # Generate CSV
            import csv
            from io import StringIO
            
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['ID', 'Name', 'Borrower', 'Total Amount', 'Remaining', 'Due Date', 'Status'])
            
            for debt in debts:
                writer.writerow([
                    debt.id,
                    debt.name,
                    debt.borrower.name if debt.borrower else '',
                    str(debt.total_amount),
                    str(debt.remaining_amount),
                    debt.due_date.isoformat(),
                    debt.status
                ])
            
            return {
                'format': 'csv',
                'data': output.getvalue(),
                'filename': f'debts_export_{timezone.now().strftime("%Y%m%d")}.csv'
            }
        else:
            # Return JSON
            data = DebtListSerializer(debts, many=True).data
            return {
                'format': 'json',
                'data': data,
                'filename': f'debts_export_{timezone.now().strftime("%Y%m%d")}.json'
            }
        
    