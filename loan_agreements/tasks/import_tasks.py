# loan_agreements/tasks/import_tasks.py
import logging

from celery import shared_task

from loan_agreements.services.loan_agreement import LoanAgreementService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def process_loan_agreement_bulk_import(self, file_path: str, user: str = 'system', request_data=None):
    """
    Process bulk import of loan agreements from CSV file.
    """
    logger.info(f"[LOAN AGREEMENT TASK] Starting bulk import from {file_path}")

    try:
        result = LoanAgreementService.import_from_csv(
            file_path=file_path,
            user=user,
            request=request_data
        )

        imported_count = len(result.get('imported', []))
        errors_count = len(result.get('errors', []))

        NotificationService.notify_admins_and_staff(
            title='📥 Loan Agreement Import Completed',
            message=f'Import completed: {imported_count} imported, {errors_count} failed.',
            type='info' if errors_count == 0 else 'error',
            metadata=result,
            user=user
        )

        logger.info(f"[LOAN AGREEMENT TASK] Bulk import completed: {imported_count} imported")
        return result

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Bulk import failed: {e}")
        raise self.retry(exc=e, countdown=120)