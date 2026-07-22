# sync/tasks/sync_maintenance.py
import logging
from datetime import timedelta
from typing import Optional

from celery import shared_task
from django.db.models import F
from django.utils import timezone

from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_conflict import SyncConflict
from sync.models.sync_queue import SyncQueue
from sync.models.task_progress import TaskProgress
from sync.services.sync_metadata import SyncMetadataService
from sync.services.sync_conflict import SyncConflictService
from sync.services.sync_queue import SyncQueueService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task
def cleanup_stale_tasks(days=7):
    """
    Clean up old TaskProgress records (completed/failed) after N days.
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = TaskProgress.objects.filter(
        status__in=["completed", "failed"], updated_at__lt=cutoff
    ).delete()
    logger.info(f"[Cleanup] Deleted {deleted_count} stale task records")
    return {"deleted": deleted_count}


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def auto_retry_failed_queue_items(self, entity: Optional[str] = None, limit: int = 50):
    """
    Automatically retry failed queue items that are eligible for retry.

    Args:
        entity: Optional filter by entity
        limit: Maximum number of items to process

    Returns:
        dict: {
            'retried': int,
            'skipped': int,
            'errors': list
        }
    """
    logger.info("[SYNC QUEUE RETRY] Starting auto-retry for failed queue items...")

    try:
        qs = SyncQueue.objects.filter(
            status=SyncQueue.Status.FAILED, retry_count__lt=F("max_retries")
        ).order_by("created_at")

        if entity:
            qs = qs.filter(entity=entity)

        items = qs[:limit]
        total = items.count()
        retried = 0
        skipped = 0
        errors = []

        for item in items:
            try:
                item.reset_for_retry()
                retried += 1
                logger.debug(f"[SYNC QUEUE RETRY] Reset queue item #{item.id} for retry")
            except Exception as e:
                skipped += 1
                errors.append(
                    {
                        "queue_id": item.id,
                        "entity": item.entity,
                        "entity_id": item.entity_id,
                        "error": str(e),
                    }
                )
                logger.error(f"[SYNC QUEUE RETRY] Failed to reset item #{item.id}: {e}")

        if retried > 0:
            logger.info(f"[SYNC QUEUE RETRY] Retried {retried} queue items ({skipped} skipped)")
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Sync Queue Auto-Retry Completed",
                    message=f"Retried {retried} failed queue items. {skipped} skipped, {len(errors)} errors.",
                    type="info" if not errors else "error",
                    metadata={
                        "retried": retried,
                        "skipped": skipped,
                        "errors": errors[:5],
                    },
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[SYNC QUEUE RETRY] Could not send notification: {e}")

        return {
            "retried": retried,
            "skipped": skipped,
            "errors": errors,
            "total_checked": total,
        }

    except Exception as e:
        logger.exception("[SYNC QUEUE RETRY] Auto-retry failed")
        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_stale_sync_metadata(self, days: int = 90):
    """
    Clean up old sync metadata records (completed/failed) older than N days.

    Args:
        days: Age in days to keep (default: 90)

    Returns:
        dict: {
            'deleted_metadata': int,
            'deleted_queue': int,
            'deleted_conflicts': int,
            'deleted_tasks': int
        }
    """
    logger.info(f"[SYNC CLEANUP] Starting stale sync metadata cleanup (older than {days} days)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)
        results = {}

        # 1. Delete old completed/failed metadata
        deleted_meta, _ = SyncMetadata.objects.filter(
            status__in=[SyncMetadata.Status.COMPLETED, SyncMetadata.Status.FAILED],
            updated_at__lt=cutoff,
            deleted_at__isnull=True,
        ).delete()
        results["deleted_metadata"] = deleted_meta

        # 2. Delete completed/failed queue items older than cutoff
        deleted_queue, _ = SyncQueue.objects.filter(
            status__in=[SyncQueue.Status.COMPLETED, SyncQueue.Status.FAILED],
            updated_at__lt=cutoff,
            deleted_at__isnull=True,
        ).delete()
        results["deleted_queue"] = deleted_queue

        # 3. Delete resolved conflicts older than cutoff
        deleted_conflicts, _ = SyncConflict.objects.filter(
            resolution__in=[
                SyncConflict.Resolution.LOCAL,
                SyncConflict.Resolution.SERVER,
                SyncConflict.Resolution.MANUAL,
                SyncConflict.Resolution.MERGED,
            ],
            resolved_at__lt=cutoff,
            deleted_at__isnull=True,
        ).delete()
        results["deleted_conflicts"] = deleted_conflicts

        # 4. Delete stale task progress records (completed/failed > 30 days)
        task_cutoff = timezone.now() - timedelta(days=30)
        deleted_tasks, _ = TaskProgress.objects.filter(
            status__in=["completed", "failed"], updated_at__lt=task_cutoff
        ).delete()
        results["deleted_tasks"] = deleted_tasks

        total_deleted = sum(results.values())
        logger.info(f"[SYNC CLEANUP] Deleted {total_deleted} stale sync records")

        if total_deleted > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🧹 Sync System Cleanup Completed",
                    message=f"Cleaned up {total_deleted} stale sync records: "
                    + f"{deleted_meta} metadata, {deleted_queue} queue, "
                    + f"{deleted_conflicts} conflicts, {deleted_tasks} tasks.",
                    type="info",
                    metadata=results,
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[SYNC CLEANUP] Could not send notification: {e}")

        return results

    except Exception as e:
        logger.exception("[SYNC CLEANUP] Cleanup failed")
        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task
def force_queue_retry(entity: Optional[str] = None, limit: int = 50):
    """Wrapper for manual trigger of queue retry."""
    logger.info("[SYNC QUEUE RETRY] 🔄 Force queue retry triggered")
    return auto_retry_failed_queue_items(entity=entity, limit=limit)