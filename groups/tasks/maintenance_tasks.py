# groups/tasks/maintenance_tasks.py
import logging
from datetime import timedelta
from typing import Optional
from django.db.models import Q
from celery import shared_task
from django.utils import timezone

from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from groups.services.group import GroupService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def update_group_statistics(self, group_id: Optional[int] = None):
    """
    Recalculate and update statistics for all groups or a specific group.
    """
    logger.info("[GROUP TASK] Updating group statistics...")

    try:
        qs = DebtorGroup.objects.filter(deleted_at__isnull=True)
        if group_id:
            qs = qs.filter(id=group_id)

        groups_updated = 0
        stats_result = []

        for group in qs:
            stats = GroupService.get_group_stats(group.id)
            stats_result.append(stats)
            groups_updated += 1

        return {
            'groups_updated': groups_updated,
            'stats': stats_result
        }

    except Exception as e:
        logger.error(f"[GROUP TASK] Stats update failed: {e}")
        raise self.retry(exc=e, countdown=300)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_orphaned_memberships(self, days: int = 30, user: str = 'system'):
    """
    Remove or soft-delete group memberships where the borrower is already deleted.
    """
    logger.info("[GROUP TASK] Starting orphaned memberships cleanup...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

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