import logging
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class BorrowerService:
    """
    Service layer for Borrower CRUD operations.
    """

    @staticmethod
    def get_by_id(borrower_id, include_deleted=False):
        """
        Get a single borrower by ID.
        """
        qs = Borrower.objects.all()
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=borrower_id)
        except Borrower.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='name', sort_order='asc'):
        """
        Get paginated list of borrowers with filters.
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
            if filters.get('include_deleted'):
                qs = Borrower.objects.all()  # Remove deleted filter
        
        # Apply sorting
        if sort_order.lower() == 'desc':
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)
        
        # Paginate
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new borrower.
        """
        # Validate unique email
        if data.get('email'):
            if Borrower.objects.filter(email=data['email']).exists():
                raise ValidationError({'email': 'Email already exists.'})
        
        # Validate unique contact
        if data.get('contact'):
            if Borrower.objects.filter(contact=data['contact']).exists():
                raise ValidationError({'contact': 'Contact already exists.'})
        
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
    def update(borrower_id, data, user=None, request=None):
        """
        Update an existing borrower.
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
                changes={'data': data}
            )
        
        logger.info(f"Borrower updated: {borrower.id} - {borrower.name}")
        return borrower

    @staticmethod
    @transaction.atomic
    def delete(borrower_id, user=None, request=None):
        """
        Soft delete a borrower.
        """
        borrower = BorrowerService.get_by_id(borrower_id)
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})
        
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
    def restore(borrower_id, user=None, request=None):
        """
        Restore a soft-deleted borrower.
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
    def permanent_delete(borrower_id, user=None, request=None):
        """
        Permanently delete a borrower (hard delete).
        """
        borrower = Borrower.objects.filter(id=borrower_id).first()
        if not borrower:
            raise ValidationError({'id': 'Borrower not found.'})
        
        borrower.delete()  # Hard delete
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='borrower_permanent_delete',
                model_name='Borrower',
                object_id=str(borrower.id),
                changes={'permanent': True}
            )
        
        logger.info(f"Borrower permanently deleted: {borrower.id} - {borrower.name}")
        return True

    @staticmethod
    def get_statistics():
        """
        Get borrower statistics.
        """
        total = Borrower.objects.filter(deleted_at__isnull=True).count()
        with_email = Borrower.objects.filter(
            deleted_at__isnull=True,
            email__isnull=False
        ).count()
        with_contact = Borrower.objects.filter(
            deleted_at__isnull=True,
            contact__isnull=False
        ).count()
        
        # Recently added (last 30 days)
        thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
        recently_added = Borrower.objects.filter(
            deleted_at__isnull=True,
            created_at__gte=thirty_days_ago
        ).count()
        
        # With active debts
        from debts.models.debt import Debt
        with_active_debts = Borrower.objects.filter(
            deleted_at__isnull=True,
            debts__deleted_at__isnull=True,
            debts__status__in=['active', 'overdue']
        ).distinct().count()
        
        return {
            'total': total,
            'with_email': with_email,
            'with_contact': with_contact,
            'recently_added': recently_added,
            'with_active_debts': with_active_debts,
        }