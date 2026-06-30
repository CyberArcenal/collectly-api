import logging
from django.db import transaction
from django.core.exceptions import ValidationError

from audit.utils.log import log_audit_event
from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember

logger = logging.getLogger(__name__)


class DebtorGroupStateTransitionService:
    """
    Service for handling debtor group state transitions.

    Handles group creation, updates, and deletion events.
    Manages audit logging and validation.
    """

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_created(group, user="system", request=None):
        """
        Handle post-group creation events.

        Args:
            group: DebtorGroup instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[DebtorGroupTransition] on_created: group_id={group.id}, name={group.name}, user={user}")

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='group_create',
            model_name='DebtorGroup',
            object_id=str(group.id),
            changes={
                'name': group.name,
                'description': group.description,
                'color': group.color,
            }
        )

        # TODO: Initialize group-level settings (e.g., default interest rate override)
        # This can be done via system settings with a key prefix like "group_{group.id}_*"

        logger.info(f"[DebtorGroupTransition] Group \"{group.name}\" created")

    @staticmethod
    @transaction.atomic
    def on_before_update(old_group, new_group_data, user="system", request=None):
        """
        Validate before group update.

        Args:
            old_group: Existing DebtorGroup instance
            new_group_data: Dict of new data
            user: User performing the action
            request: HTTP request object for audit

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[DebtorGroupTransition] on_before_update: group_id={old_group.id}, user={user}")

        # Validate name uniqueness (case-insensitive)
        if new_group_data.get('name') and new_group_data['name'] != old_group.name:
            existing = DebtorGroup.objects.filter(
                name__iexact=new_group_data['name'],
                deleted_at__isnull=True
            ).exclude(id=old_group.id).first()

            if existing:
                raise ValidationError({
                    'name': f'Group name "{new_group_data["name"]}" already exists.'
                })

    @staticmethod
    @transaction.atomic
    def on_after_update(old_group, new_group, user="system", request=None):
        """
        Handle post-group update events.

        Args:
            old_group: Old DebtorGroup instance
            new_group: Updated DebtorGroup instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[DebtorGroupTransition] on_after_update: group_id={new_group.id}, name={new_group.name}, user={user}")

        # Track changes for audit
        changes = {}
        if old_group.name != new_group.name:
            changes['name'] = {'old': old_group.name, 'new': new_group.name}
        if old_group.description != new_group.description:
            changes['description'] = {'old': old_group.description, 'new': new_group.description}
        if old_group.color != new_group.color:
            changes['color'] = {'old': old_group.color, 'new': new_group.color}

        # Audit log if there were changes
        if changes:
            log_audit_event(
                request=request,
                user=user,
                action_type='group_update',
                model_name='DebtorGroup',
                object_id=str(new_group.id),
                changes=changes
            )

        # TODO: If name or color changed, update UI caches
        # Frontend will reload on next request

        logger.info(f"[DebtorGroupTransition] Group \"{new_group.name}\" updated")

    @staticmethod
    @transaction.atomic
    def on_before_delete(group, user="system", request=None):
        """
        Handle pre-group deletion validation.

        Args:
            group: DebtorGroup instance
            user: User performing the action
            request: HTTP request object for audit

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[DebtorGroupTransition] on_before_delete: group_id={group.id}, name={group.name}, user={user}")

        # Check if group has members
        member_count = DebtorGroupMember.objects.filter(
            group=group,
            deleted_at__isnull=True
        ).count()

        if member_count > 0:
            logger.warning(
                f"[DebtorGroupTransition] Group \"{group.name}\" has {member_count} members; "
                f"they will be removed by cascade."
            )
            # Allow deletion - cascade will remove members

        # TODO: Prevent deletion if group is used in active rules/reports
        # if group.is_used_in_reports():
        #     raise ValidationError({
        #         'detail': f'Group "{group.name}" is used in reports and cannot be deleted.'
        #     })

    @staticmethod
    @transaction.atomic
    def on_after_delete(group, user="system", request=None):
        """
        Handle post-group deletion events.

        Args:
            group: DebtorGroup instance (before deletion)
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[DebtorGroupTransition] on_after_delete: group_id={group.id}, name={group.name}, user={user}")

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='group_delete',
            model_name='DebtorGroup',
            object_id=str(group.id),
            changes={
                'name': group.name,
                'description': group.description,
                'color': group.color,
            }
        )

        # Memberships are automatically removed by CASCADE

        # TODO: Archive or delete group-based reports
        # If any report configuration uses this group, update those reports

        logger.info(f"[DebtorGroupTransition] Group \"{group.name}\" deleted")