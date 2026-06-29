import logging
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from audit.utils.log import log_audit_event
from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from borrowers.models.borrower import Borrower
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class GroupService:
    """
    Service layer for Group and GroupMember operations.
    """

    # ============================================================
    # GROUP CRUD
    # ============================================================

    @staticmethod
    def get_group_by_id(group_id):
        """
        Get a single group by ID.
        """
        try:
            return DebtorGroup.objects.get(id=group_id, deleted_at__isnull=True)
        except DebtorGroup.DoesNotExist:
            return None

    @staticmethod
    def get_groups(page=1, limit=20, search=None):
        """
        Get paginated list of groups.
        """
        qs = DebtorGroup.objects.filter(deleted_at__isnull=True)
        
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search)
            )
        
        qs = qs.order_by('name')
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def create_group(data, user=None, request=None):
        """
        Create a new group.
        """
        # Validate unique name
        if DebtorGroup.objects.filter(name=data['name']).exists():
            raise ValidationError({'name': 'Group name already exists.'})
        
        group = DebtorGroup.objects.create(
            name=data['name'],
            description=data.get('description'),
            color=data.get('color', '#3b82f6')
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_create',
                model_name='DebtorGroup',
                object_id=str(group.id),
                changes={'data': data}
            )
        
        logger.info(f"Group created: {group.id} - {group.name}")
        return group

    @staticmethod
    @transaction.atomic
    def update_group(group_id, data, user=None, request=None):
        """
        Update an existing group.
        """
        group = GroupService.get_group_by_id(group_id)
        if not group:
            raise ValidationError({'id': 'Group not found.'})
        
        # Check unique name if changed
        if data.get('name') and data['name'] != group.name:
            if DebtorGroup.objects.filter(name=data['name']).exists():
                raise ValidationError({'name': 'Group name already exists.'})
            group.name = data['name']
        
        if 'description' in data:
            group.description = data['description']
        if 'color' in data:
            group.color = data['color']
        
        group.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_update',
                model_name='DebtorGroup',
                object_id=str(group.id),
                changes={'data': data}
            )
        
        logger.info(f"Group updated: {group.id} - {group.name}")
        return group

    @staticmethod
    @transaction.atomic
    def delete_group(group_id, user=None, request=None):
        """
        Soft delete a group (cascade to members).
        """
        group = GroupService.get_group_by_id(group_id)
        if not group:
            raise ValidationError({'id': 'Group not found.'})
        
        # Soft delete all members first
        group.members.filter(deleted_at__isnull=True).update(deleted_at=timezone.now())
        
        group.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_delete',
                model_name='DebtorGroup',
                object_id=str(group.id),
                changes={'deleted_at': group.deleted_at}
            )
        
        logger.info(f"Group soft-deleted: {group.id} - {group.name}")
        return group

    # ============================================================
    # GROUP MEMBERS CRUD
    # ============================================================

    @staticmethod
    def get_members(group_id, page=1, limit=20):
        """
        Get paginated list of members in a group.
        """
        group = GroupService.get_group_by_id(group_id)
        if not group:
            raise ValidationError({'group_id': 'Group not found.'})
        
        qs = group.members.filter(
            deleted_at__isnull=True,
            debtor__deleted_at__isnull=True
        ).select_related('debtor').order_by('-assigned_at')
        
        return paginate_queryset(qs, page, limit)

    @staticmethod
    def get_groups_for_borrower(borrower_id, page=1, limit=20):
        """
        Get paginated list of groups a borrower belongs to.
        """
        qs = DebtorGroupMember.objects.filter(
            debtor_id=borrower_id,
            deleted_at__isnull=True
        ).select_related('group').order_by('-assigned_at')
        
        result = paginate_queryset(qs, page, limit)
        # Extract groups from members
        result['data'] = [item.group for item in result['data']]
        return result

    @staticmethod
    @transaction.atomic
    def add_member(group_id, debtor_id, user=None, request=None):
        """
        Add a borrower to a group.
        """
        group = GroupService.get_group_by_id(group_id)
        if not group:
            raise ValidationError({'group_id': 'Group not found.'})
        
        debtor = Borrower.objects.filter(id=debtor_id, deleted_at__isnull=True).first()
        if not debtor:
            raise ValidationError({'debtor_id': 'Borrower not found.'})
        
        # Check if already a member (including soft-deleted)
        existing = DebtorGroupMember.objects.filter(
            group=group,
            debtor=debtor
        ).first()
        
        if existing:
            if existing.deleted_at:
                # Restore soft-deleted membership
                existing.deleted_at = None
                existing.save()
                logger.info(f"Member restored: {debtor.name} → {group.name}")
                return existing
            else:
                raise ValidationError({'debtor_id': f'Borrower is already a member of {group.name}.'})
        
        member = DebtorGroupMember.objects.create(
            group=group,
            debtor=debtor
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_member_add',
                model_name='DebtorGroupMember',
                object_id=str(member.id),
                changes={'group_id': group_id, 'debtor_id': debtor_id}
            )
        
        logger.info(f"Member added: {debtor.name} → {group.name}")
        return member

    @staticmethod
    @transaction.atomic
    def remove_member(group_id, debtor_id, user=None, request=None):
        """
        Remove a borrower from a group (soft delete membership).
        """
        member = DebtorGroupMember.objects.filter(
            group_id=group_id,
            debtor_id=debtor_id,
            deleted_at__isnull=True
        ).first()
        
        if not member:
            raise ValidationError({'detail': 'Member not found in this group.'})
        
        member.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_member_remove',
                model_name='DebtorGroupMember',
                object_id=str(member.id),
                changes={'group_id': group_id, 'debtor_id': debtor_id}
            )
        
        logger.info(f"Member removed: {member.debtor.name} from {member.group.name}")
        return member

    @staticmethod
    def get_group_stats(group_id):
        """
        Get statistics for a group.
        """
        group = GroupService.get_group_by_id(group_id)
        if not group:
            raise ValidationError({'id': 'Group not found.'})
        
        members = group.members.filter(
            deleted_at__isnull=True,
            debtor__deleted_at__isnull=True
        ).select_related('debtor')
        
        total_members = members.count()
        
        # Calculate total debt of members
        total_debt = sum(
            member.debtor.total_debt for member in members
        )
        
        return {
            'group_id': group.id,
            'name': group.name,
            'total_members': total_members,
            'total_debt': total_debt,
        }