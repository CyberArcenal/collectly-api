# sync/tasks/sync_health.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from sync.models.sync_conflict import SyncConflict
from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_queue import SyncQueue
from sync.models.task_progress import TaskProgress
from sync.services.sync_metadata import SyncMetadataService
from sync.services.sync_conflict import SyncConflictService
from sync.services.sync_queue import SyncQueueService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


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

        if issues:
            logger.warning(
                f"[SYNC HEALTH] Health check found {len(issues)} issues: {[i['message'] for i in issues]}"
            )

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


@shared_task
def force_sync_health_check():
    """Wrapper for manual trigger of sync health check."""
    logger.info("[SYNC HEALTH] 🔄 Force health check triggered")
    return sync_health_check()


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

        metadata_summary = SyncMetadataService.get_summary()
        queue_stats = SyncQueueService.get_statistics()
        conflict_stats = SyncConflictService.get_statistics()

        tasks_completed = TaskProgress.objects.filter(
            status="completed", created_at__gte=cutoff
        ).count()
        tasks_failed = TaskProgress.objects.filter(
            status="failed", created_at__gte=cutoff
        ).count()

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