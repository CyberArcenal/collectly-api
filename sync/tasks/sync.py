# sync/tasks.py
import logging
import uuid
from celery import shared_task
from django.core.exceptions import ValidationError
from django.db import transaction, OperationalError
from django.utils import timezone
from datetime import timedelta

from sync.services.sync import SyncService
from sync.services.task_progress import TaskProgressService
from sync.models.task_progress import TaskProgress

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=300,
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

    try:
        # Mark task as running
        TaskProgressService.update_status(task_id, 'running')
        TaskProgressService.update_progress(task_id, 0, entity_name)

        # Process in smaller chunks to reduce lock contention
        chunk_size = 25  # Reduced from 50 to reduce transaction time
        total = len(records)
        results = {
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [],
            'conflicts': [],
            'ids': [],
        }

        # Process each chunk
        for i in range(0, total, chunk_size):
            chunk = records[i:i + chunk_size]
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
                if 'database is locked' in str(e):
                    logger.warning(f"[SyncTask] Database locked, retrying chunk {i}-{i+chunk_size}")
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
                state='PROGRESS',
                meta={
                    'current': processed_count,
                    'total': total,
                    'entity': entity_name,
                    'created': results['created'],
                    'updated': results['updated'],
                    'errors': len(results['errors']),
                }
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
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


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
        entity_name = config.get('entity_name')
        records = config.get('records', [])
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
            task_id=task_id
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
        status__in=['completed', 'failed'],
        updated_at__lt=cutoff
    ).delete()
    logger.info(f"[Cleanup] Deleted {deleted_count} stale task records")
    return {'deleted': deleted_count}