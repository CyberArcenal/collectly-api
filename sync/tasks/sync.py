# sync/tasks.py
import logging
from typing import Optional
import uuid
from celery import shared_task
from django.core.exceptions import ValidationError
from django.db import transaction, OperationalError
from django.utils import timezone
from datetime import timedelta
from django.db.models import F
from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_conflict import SyncConflict
from sync.models.sync_queue import SyncQueue
from sync.models.task_progress import TaskProgress
from sync.services.sync_metadata import SyncMetadataService
from sync.services.sync_conflict import SyncConflictService
from sync.services.sync_queue import SyncQueueService
from notifications.services.notification import NotificationService
from sync.services.sync import SyncService
from sync.services.task_progress import TaskProgressService
from sync.models.task_progress import TaskProgress
import time
import fcntl

logger = logging.getLogger(__name__)
LOCK_FILE = "/tmp/sync_task.lock"


def acquire_lock(timeout=30):
    """Acquire a file-based lock to prevent concurrent sync tasks."""
    lock_fd = open(LOCK_FILE, "w")
    start = time.time()
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except OSError:
            if time.time() - start > timeout:
                raise TimeoutError("Could not acquire lock for sync task")
            time.sleep(1)


def release_lock(lock_fd):
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()


@shared_task(
    bind=True,
    max_retries=10,
    default_retry_delay=60,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def sync_entity_task(self, entity_name, records, client_user, task_id):
    """
    Background task to process sync for a specific entity.

    Features:
    - Chunk processing with per-chunk transaction (reduces locks)
    - Retry on database locks with exponential backoff
    - Validation of required fields (handled by SyncService)
    - Detailed progress updates
    """
    logger.info(
        f"[SyncTask] Starting sync for {entity_name}: {len(records)} records, "
        f"task_id={task_id}, retry={self.request.retries}"
    )
    lock_fd = None
    try:
        lock_fd = acquire_lock(timeout=60)
        # Mark task as running
        TaskProgressService.update_status(task_id, "running")
        TaskProgressService.update_progress(task_id, 0, entity_name)

        # Process in smaller chunks to reduce lock contention
        chunk_size = 25  # Reduced from 50 to reduce transaction time
        total = len(records)
        results = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
            "conflicts": [],
            "ids": [],
        }

        # Process each chunk
        for i in range(0, total, chunk_size):
            chunk = records[i : i + chunk_size]
            processed_count = min(i + chunk_size, total)

            # Use a transaction per chunk, but with a timeout to avoid long locks
            try:
                with transaction.atomic():
                    chunk_result = SyncService.pull_sync_chunk(
                        entity_name=entity_name,
                        records=chunk,
                        client_user=client_user,
                    )
            except OperationalError as e:
                # If database is locked, retry the entire chunk with a delay
                if "database is locked" in str(e):
                    logger.warning(
                        f"[SyncTask] Database locked, retrying chunk {i}-{i+chunk_size}"
                    )
                    # Retry the chunk after a short delay
                    import time

                    time.sleep(2)  # Wait 2 seconds before retrying the chunk
                    # Retry the chunk once more
                    with transaction.atomic():
                        chunk_result = SyncService.pull_sync_chunk(
                            entity_name=entity_name,
                            records=chunk,
                            client_user=client_user,
                        )
                else:
                    raise

            # Merge results
            for key in results:
                if key in chunk_result:
                    if isinstance(results[key], list):
                        results[key].extend(chunk_result[key])
                    else:
                        results[key] += chunk_result[key]

            # Update progress after each chunk
            TaskProgressService.update_progress(task_id, processed_count, entity_name)
            TaskProgressService.update_result(task_id, results)

            # Update Celery's own state for Flower monitoring
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": processed_count,
                    "total": total,
                    "entity": entity_name,
                    "created": results["created"],
                    "updated": results["updated"],
                    "errors": len(results["errors"]),
                },
            )

            logger.debug(
                f"[SyncTask] {entity_name} progress: {processed_count}/{total} "
                f"(+{len(chunk)} records, created={results['created']}, updated={results['updated']})"
            )

        # Mark as completed
        TaskProgressService.mark_completed(task_id, results)
        logger.info(
            f"[SyncTask] Completed sync for {entity_name}: "
            f"{results['created']} created, {results['updated']} updated, "
            f"{len(results['errors'])} errors, {len(results['conflicts'])} conflicts"
        )
        return results

    except TimeoutError as e:
        logger.error(f"[SyncTask] Could not acquire lock: {e}")
        raise self.retry(exc=e, countdown=30)

    except OperationalError as e:
        # If it's a disk I/O error, retry with backoff
        if "disk I/O error" in str(e) or "database is locked" in str(e):
            logger.warning(f"[SyncTask] Database error, retrying: {e}")
            raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
        raise

    except Exception as exc:
        logger.exception(f"[SyncTask] Failed for {entity_name}: {exc}")

        # Mark as failed
        TaskProgressService.mark_failed(task_id, str(exc))

        # Retry the task if it's not a permanent error and we have retries left
        if isinstance(exc, (ValidationError, ValueError, AttributeError)):
            # These are permanent errors, don't retry
            logger.warning(f"[SyncTask] Permanent error, not retrying: {exc}")
            raise exc

        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=30 * (2**self.request.retries))

    finally:
        if lock_fd:
            release_lock(lock_fd)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def sync_multiple_entities_task(self, sync_configs, client_user):
    """
    Task to sync multiple entities in sequence.
    """
    results = {}
    for config in sync_configs:
        entity_name = config.get("entity_name")
        records = config.get("records", [])
        if not entity_name or not records:
            continue

        # Create a task_id for each entity
        task_id = str(uuid.uuid4())
        TaskProgressService.create_task(task_id, entity_name, len(records))

        # Run the sync task synchronously (or chain)
        result = sync_entity_task(
            entity_name=entity_name,
            records=records,
            client_user=client_user,
            task_id=task_id,
        )
        results[entity_name] = result

    return results


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


# ============================================================
# NEW TASKS
# ============================================================


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def sync_health_check(self):
    """
    Perform a health check on the sync system.

    Checks:
    - Stuck tasks (running for > 1 hour)
    - High conflict count (> 10)
    - Missing metadata for entities
    - Pending queue items stuck in processing

    Returns:
        dict: {
            'status': 'healthy' | 'degraded' | 'unhealthy',
            'issues': list,
            'summary': dict
        }
    """
    logger.info("[SYNC HEALTH] Starting sync system health check...")

    try:
        issues = []
        summary = {}

        # 1. Check for stuck tasks (running > 1 hour)
        one_hour_ago = timezone.now() - timedelta(hours=1)
        stuck_tasks = TaskProgress.objects.filter(
            status="running", updated_at__lt=one_hour_ago
        ).count()
        summary["stuck_tasks"] = stuck_tasks
        if stuck_tasks > 0:
            issues.append(
                {
                    "type": "stuck_tasks",
                    "count": stuck_tasks,
                    "message": f"{stuck_tasks} task(s) have been running for more than 1 hour.",
                }
            )

        # 2. Check for high conflicts
        conflict_count = SyncConflict.objects.filter(
            resolution=SyncConflict.Resolution.PENDING
        ).count()
        summary["pending_conflicts"] = conflict_count
        if conflict_count > 10:
            issues.append(
                {
                    "type": "high_conflicts",
                    "count": conflict_count,
                    "message": f"{conflict_count} pending conflicts exceed threshold (10).",
                }
            )

        # 3. Check for missing metadata entities
        from sync.services.sync import ENTITY_CONFIG

        existing_entities = set(
            SyncMetadata.objects.filter(deleted_at__isnull=True).values_list(
                "entity", flat=True
            )
        )
        expected_entities = set(ENTITY_CONFIG.keys())
        missing_entities = expected_entities - existing_entities
        summary["missing_entities"] = list(missing_entities)
        if missing_entities:
            issues.append(
                {
                    "type": "missing_metadata",
                    "entities": list(missing_entities),
                    "message": f'Missing sync metadata for: {", ".join(missing_entities)}',
                }
            )

        # 4. Check for queue items stuck in processing (> 30 min)
        thirty_min_ago = timezone.now() - timedelta(minutes=30)
        stuck_queue = SyncQueue.objects.filter(
            status=SyncQueue.Status.PROCESSING, updated_at__lt=thirty_min_ago
        ).count()
        summary["stuck_queue_items"] = stuck_queue
        if stuck_queue > 0:
            issues.append(
                {
                    "type": "stuck_queue_items",
                    "count": stuck_queue,
                    "message": f"{stuck_queue} queue item(s) stuck in processing for > 30 minutes.",
                }
            )

        # Determine overall status
        if issues:
            if len(issues) >= 3 or any(
                i["type"] in ["stuck_tasks", "stuck_queue_items"] for i in issues
            ):
                status = "unhealthy"
            else:
                status = "degraded"
        else:
            status = "healthy"

        # Log issues
        if issues:
            logger.warning(
                f"[SYNC HEALTH] Health check found {len(issues)} issues: {[i['message'] for i in issues]}"
            )

            # Send notification to admins if unhealthy
            if status in ["degraded", "unhealthy"]:
                try:
                    NotificationService.notify_admins_and_staff(
                        title=f"⚠️ Sync System Health: {status.upper()}",
                        message=f"Sync health check found {len(issues)} issues.\n\n"
                        + "\n".join([f'- {i["message"]}' for i in issues]),
                        type="error" if status == "unhealthy" else "warning",
                        metadata={
                            "status": status,
                            "issues": issues,
                            "summary": summary,
                        },
                        user="system",
                    )
                except Exception as e:
                    logger.warning(f"[SYNC HEALTH] Could not send notification: {e}")

        logger.info(f"[SYNC HEALTH] Health check completed: {status}")
        return {"status": status, "issues": issues, "summary": summary}

    except Exception as e:
        logger.exception("[SYNC HEALTH] Health check failed")
        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


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
        # Find failed items that can still be retried
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
                # Reset for retry
                item.reset_for_retry()
                retried += 1
                logger.debug(
                    f"[SYNC QUEUE RETRY] Reset queue item #{item.id} for retry"
                )
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

        # Log summary
        if retried > 0:
            logger.info(
                f"[SYNC QUEUE RETRY] Retried {retried} queue items ({skipped} skipped)"
            )
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
    logger.info(
        f"[SYNC CLEANUP] Starting stale sync metadata cleanup (older than {days} days)..."
    )

    try:
        cutoff = timezone.now() - timedelta(days=days)
        results = {}

        # 1. Delete old completed/failed metadata (but keep last_sync info)
        # We'll only delete records that are completed/failed and haven't been updated in days
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
def force_sync_health_check():
    """Wrapper for manual trigger of sync health check."""
    logger.info("[SYNC HEALTH] 🔄 Force health check triggered")
    return sync_health_check()


@shared_task
def force_queue_retry(entity: Optional[str] = None, limit: int = 50):
    """Wrapper for manual trigger of queue retry."""
    logger.info("[SYNC QUEUE RETRY] 🔄 Force queue retry triggered")
    return auto_retry_failed_queue_items(entity=entity, limit=limit)


@shared_task
def generate_sync_report(days: int = 7, user: str = "system"):
    """
    Generate a sync activity report for the last N days.

    Args:
        days: Number of days to report on
        user: User to send the report to

    Returns:
        dict: Report data
    """
    logger.info(f"[SYNC REPORT] Generating sync report for last {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Get sync metadata summary
        metadata_summary = SyncMetadataService.get_summary()

        # Queue statistics
        queue_stats = SyncQueueService.get_statistics()

        # Conflict statistics
        conflict_stats = SyncConflictService.get_statistics()

        # Tasks completed in period
        tasks_completed = TaskProgress.objects.filter(
            status="completed", created_at__gte=cutoff
        ).count()

        tasks_failed = TaskProgress.objects.filter(
            status="failed", created_at__gte=cutoff
        ).count()

        # Recent syncs (last 7 days metadata updates)
        recent_syncs = SyncMetadata.objects.filter(
            last_synced_at__gte=cutoff, deleted_at__isnull=True
        ).count()

        report = {
            "generated_at": timezone.now().isoformat(),
            "period_days": days,
            "metadata_summary": metadata_summary,
            "queue_stats": queue_stats,
            "conflict_stats": conflict_stats,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "recent_syncs": recent_syncs,
            "entities_with_syncs": list(
                SyncMetadata.objects.filter(
                    last_synced_at__gte=cutoff, deleted_at__isnull=True
                ).values("entity", "last_synced_at", "total_synced")
            ),
        }

        # Send report to admins
        try:
            NotificationService.notify_admins_and_staff(
                title="📊 Weekly Sync Report",
                message=f"Sync report for the last {days} days: "
                + f'{metadata_summary["total_entities"]} entities, '
                + f'{metadata_summary["total_synced"]} total records synced, '
                + f"{tasks_completed} tasks completed, {tasks_failed} failed.",
                type="info",
                metadata=report,
                user=user,
            )
        except Exception as e:
            logger.warning(f"[SYNC REPORT] Could not send notification: {e}")

        logger.info("[SYNC REPORT] Report generated successfully")
        return report

    except Exception as e:
        logger.exception("[SYNC REPORT] Report generation failed")
        raise
