# loan_applications/tasks/import_tasks.py
import logging
from typing import Optional

from celery import shared_task

from loan_applications.services.loan_application import LoanApplicationService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def bulk_import_applications(
    self, file_path: str, user: str = "system", request_data: Optional[dict] = None
):
    """
    Bulk import loan applications from CSV file.
    """
    logger.info(f"[LOAN APPLICATION TASK] Starting bulk import from {file_path}")

    try:
        result = LoanApplicationService.import_from_csv(
            file_path=file_path, user=user, request=request_data
        )

        NotificationService.notify_admins_and_staff(
            title="📥 Loan Application Import Completed",
            message=f'Import completed: {len(result.get("imported", []))} imported, {len(result.get("errors", []))} failed.',
            type="info" if not result.get("errors") else "error",
            metadata=result,
            user=user,
        )

        logger.info(
            f"[LOAN APPLICATION TASK] Bulk import completed: {len(result.get('imported', []))} imported"
        )
        return result

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Bulk import failed: {e}")
        NotificationService.notify_admins_and_staff(
            title="❌ Loan Application Import Failed",
            message=f"Bulk import failed: {str(e)}",
            type="error",
            metadata={"error": str(e)},
            user=user,
        )
        raise self.retry(exc=e, countdown=120)