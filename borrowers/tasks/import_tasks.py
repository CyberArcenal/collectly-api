# borrowers/tasks/import_tasks.py
import logging
from typing import Optional

from celery import shared_task

from borrowers.services.borrower import BorrowerService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def process_borrower_bulk_import(self, file_path: str, user: str = 'system', request_data: Optional[dict] = None):
    """
    Process bulk import of borrowers from CSV file.
    """
    logger.info(f"[BORROWER TASK] Starting bulk import from {file_path}")

    try:
        result = BorrowerService.import_from_csv(
            file_path=file_path,
            user=user,
            request=request_data
        )

        if result.get('imported') or result.get('errors'):
            NotificationService.notify_admins_and_staff(
                title='📥 Borrower Import Completed',
                message=f'Import completed: {len(result.get("imported", []))} imported, {len(result.get("errors", []))} failed.',
                type='info' if not result.get('errors') else 'error',
                metadata=result,
                user=user
            )

        logger.info(f"[BORROWER TASK] Bulk import completed: {len(result.get('imported', []))} imported")
        return result

    except Exception as e:
        logger.error(f"[BORROWER TASK] Bulk import failed: {e}")
        NotificationService.notify_admins_and_staff(
            title='❌ Borrower Import Failed',
            message=f'Bulk import failed: {str(e)}',
            type='error',
            metadata={'error': str(e)},
            user=user
        )
        raise self.retry(exc=e, countdown=120)