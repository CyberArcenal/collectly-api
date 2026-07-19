# groups/tasks/group.py
from datetime import timedelta
import logging
from typing import Optional, List, Dict, Any
from django.db import transaction
from django.db import models
from django.db.models import Q, Count, Sum, Value
from django.utils import timezone
from celery import shared_task

from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from groups.services.group import GroupService
from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


# ============================================================
# BULK ASSIGN TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def bulk_assign_borrowers_to_group(
    self,
    group_id: int,
    debtor_ids: List[int],
    user: str = 'system',
    batch_size: int = 100,
):
    """
    Bulk assign multiple borrowers to a group asynchronously.

    Args:
        group_id: ID of the group
        debtor_ids: List of debtor IDs to assign
        user: User performing the action
        batch_size: Number of borrowers to process per batch

    Returns:
        dict: {
            'assigned_count': int,
            'errors': list
        }
    """
    logger.info(f"[GROUP TASK] Starting bulk assign: group_id={group_id}, total={len(debtor_ids)}")

    try:
        # Verify group exists
        group = GroupService.get_group_by_id(group_id)
        if not group:
            return {
                'assigned_count': 0,
                'errors': [{'error': f'Group {group_id} not found'}]
            }

        total_assigned = 0
        all_errors = []

        # Process in batches
        for i in range(0, len(debtor_ids), batch_size):
            batch = debtor_ids[i:i + batch_size]
            logger.info(f"[GROUP TASK] Processing batch {i//batch_size + 1}: {len(batch)} borrowers")

            try:
                # Use existing bulk_assign service method
                result = GroupService.bulk_assign(
                    group_id=group_id,
                    debtor_ids=batch,
                    user=user,
                    request=None
                )
                total_assigned += result.get('assigned_count', 0)
                all_errors.extend(result.get('errors', []))
            except Exception as e:
                logger.error(f"[GROUP TASK] Batch failed: {e}")
                all_errors.append({'batch': i//batch_size + 1, 'error': str(e)})

        # Notify admins/staff
        if total_assigned > 0 or all_errors:
            NotificationService.notify_admins_and_staff(
                title='📋 Bulk Assign Completed',
                message=f'Assigned {total_assigned} borrowers to group "{group.name}", {len(all_errors)} errors.',
                type='info' if not all_errors else 'error',
                metadata={
                    'group_id': group_id,
                    'group_name': group.name,
                    'assigned': total_assigned,
                    'errors': all_errors[:10]
                },
                user=user
            )

        logger.info(f"[GROUP TASK] Bulk assign completed: {total_assigned} assigned, {len(all_errors)} errors")
        return {
            'assigned_count': total_assigned,
            'errors': all_errors
        }

    except Exception as e:
        logger.error(f"[GROUP TASK] Bulk assign failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# GROUP STATISTICS UPDATE
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def update_group_statistics(self, group_id: Optional[int] = None):
    """
    Recalculate and update statistics for all groups or a specific group.

    Args:
        group_id: Optional specific group ID to update

    Returns:
        dict: {
            'groups_updated': int,
            'stats': list
        }
    """
    logger.info(f"[GROUP TASK] Updating group statistics...")

    try:
        qs = DebtorGroup.objects.filter(deleted_at__isnull=True)
        if group_id:
            qs = qs.filter(id=group_id)

        groups_updated = 0
        stats_result = []

        for group in qs:
            stats = GroupService.get_group_stats(group.id)
            # We don't store stats yet, but we can return them.
            # If we had a GroupStatistics model, we would update it here.
            stats_result.append(stats)
            groups_updated += 1

        return {
            'groups_updated': groups_updated,
            'stats': stats_result
        }

    except Exception as e:
        logger.error(f"[GROUP TASK] Stats update failed: {e}")
        raise self.retry(exc=e, countdown=300)


# ============================================================
# CLEANUP ORPHANED MEMBERSHIPS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_orphaned_memberships(self, days: int = 30, user: str = 'system'):
    """
    Remove or soft-delete group memberships where the borrower is already deleted.

    Args:
        days: Age in days (only process memberships older than this)
        user: User performing the action

    Returns:
        dict: {
            'deleted_count': int,
            'deleted_ids': list
        }
    """
    logger.info(f"[GROUP TASK] Starting orphaned memberships cleanup...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find memberships where borrower is deleted or soft-deleted
        orphaned = DebtorGroupMember.objects.filter(
            Q(debtor__deleted_at__isnull=False) |
            Q(debtor_id__isnull=True)
        ).filter(
            deleted_at__isnull=True,
            created_at__lt=cutoff
        )

        count = orphaned.count()
        if count == 0:
            return {'deleted_count': 0, 'deleted_ids': [], 'message': 'No orphaned memberships found'}

        # Soft delete them
        ids = list(orphaned.values_list('id', flat=True))
        orphaned.update(deleted_at=timezone.now())

        logger.info(f"[GROUP TASK] Soft-deleted {count} orphaned memberships")

        NotificationService.notify_admins_and_staff(
            title='🧹 Orphaned Memberships Cleanup',
            message=f'Removed {count} orphaned group memberships.',
            type='info',
            metadata={'deleted_count': count, 'deleted_ids': ids[:20]},
            user=user
        )

        return {
            'deleted_count': count,
            'deleted_ids': ids,
            'message': f'Removed {count} orphaned memberships'
        }

    except Exception as e:
        logger.error(f"[GROUP TASK] Orphaned cleanup failed: {e}")
        raise self.retry(exc=e, countdown=300)


# ============================================================
# AUTO-ASSIGN TASK (Based on Criteria)
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_assign_borrowers_to_groups(
    self,
    group_id: int,
    criteria: Dict[str, Any],
    user: str = 'system',
    batch_size: int = 100
):
    """
    Auto-assign borrowers to a group based on criteria.

    Criteria examples:
        - credit_rating__gte: "Good" or score threshold
        - total_debt__gte: 5000
        - active_debt_count__gte: 2
        - email: True (has email)
        - contact: True (has contact)

    Args:
        group_id: ID of the group to assign to
        criteria: Dict of filter criteria
        user: User performing the action
        batch_size: Number of borrowers to process per batch

    Returns:
        dict: {
            'assigned_count': int,
            'total_candidates': int,
            'errors': list
        }
    """
    logger.info(f"[GROUP TASK] Auto-assign starting: group_id={group_id}, criteria={criteria}")

    try:
        # Verify group exists
        group = GroupService.get_group_by_id(group_id)
        if not group:
            return {
                'assigned_count': 0,
                'total_candidates': 0,
                'errors': [{'error': f'Group {group_id} not found'}]
            }

        # Build query for borrowers matching criteria
        qs = Borrower.objects.filter(deleted_at__isnull=True)

        # Apply criteria
        if criteria.get('credit_rating'):
            qs = qs.filter(credit_rating=criteria['credit_rating'])

        if criteria.get('credit_rating__in'):
            qs = qs.filter(credit_rating__in=criteria['credit_rating__in'])

        if criteria.get('has_email'):
            qs = qs.filter(email__isnull=False).exclude(email='')

        if criteria.get('has_contact'):
            qs = qs.filter(contact__isnull=False).exclude(contact='')

        # More complex criteria using annotations
        if criteria.get('total_debt__gte'):
            from django.db.models import Sum, OuterRef, Subquery
            total_debt_subquery = Debt.objects.filter(
                borrower=OuterRef('id'),
                deleted_at__isnull=True,
                status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
            ).values('borrower_id').annotate(total=Sum('remaining_amount')).values('total')
            qs = qs.annotate(total_debt=Subquery(total_debt_subquery, output_field=models.DecimalField()))
            qs = qs.filter(total_debt__gte=criteria['total_debt__gte'])

        if criteria.get('active_debt_count__gte'):
            from django.db.models import Count
            qs = qs.annotate(active_debt_count=Count('debts', filter=Q(
                debts__deleted_at__isnull=True,
                debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
            )))
            qs = qs.filter(active_debt_count__gte=criteria['active_debt_count__gte'])

        # Exclude borrowers already in the group
        already_in_group = DebtorGroupMember.objects.filter(
            group_id=group_id,
            deleted_at__isnull=True
        ).values_list('debtor_id', flat=True)
        qs = qs.exclude(id__in=already_in_group)

        total_candidates = qs.count()
        logger.info(f"[GROUP TASK] Found {total_candidates} candidates for auto-assign")

        if total_candidates == 0:
            return {
                'assigned_count': 0,
                'total_candidates': 0,
                'errors': [],
                'message': 'No candidates found matching criteria'
            }

        # Extract IDs and use bulk assign
        borrower_ids = list(qs.values_list('id', flat=True))
        result = bulk_assign_borrowers_to_group(
            group_id=group_id,
            debtor_ids=borrower_ids,
            user=user,
            batch_size=batch_size
        )

        result['total_candidates'] = total_candidates
        return result

    except Exception as e:
        logger.error(f"[GROUP TASK] Auto-assign failed: {e}")
        raise self.retry(exc=e, countdown=300)


# ============================================================
# NOTIFICATION TASK FOR GROUP CHANGES
# ============================================================

@shared_task
def notify_group_change(group_id: int, action: str, user: str = 'system'):
    """
    Send notification when a group is created, updated, or deleted.

    Args:
        group_id: ID of the group
        action: 'created', 'updated', 'deleted'
        user: User performing the action
    """
    try:
        group = GroupService.get_group_by_id(group_id)
        if not group and action != 'deleted':
            return

        group_name = group.name if group else f'Group #{group_id}'

        NotificationService.notify_admins_and_staff(
            title=f'📋 Group {action.capitalize()}',
            message=f'Group "{group_name}" has been {action}.',
            type='info',
            metadata={
                'group_id': group_id,
                'action': action,
                'user': user,
                'timestamp': timezone.now().isoformat()
            },
            user=user
        )
    except Exception as e:
        logger.error(f"[GROUP TASK] Notification failed: {e}")