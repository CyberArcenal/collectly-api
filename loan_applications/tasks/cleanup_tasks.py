# loan_applications/tasks/cleanup_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from loan_applications.models.loan_application import LoanApplication
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_stale_applications(self, days: int = 30, user: str = "system"):
    """
    Soft delete stale (old) pending or rejected applications.
    """
    logger.info(f"[LOAN APPLICATION TASK] Starting stale application cleanup (older than {days} days)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        stale_apps = LoanApplication.objects.filter(
            deleted_at__isnull=True,
            created_at__lt=cutoff,
            status__in=[
                LoanApplication.Status.PENDING,
                LoanApplication.Status.REJECTED,
            ],
        )

        count = stale_apps.count()
        if count == 0:
            return {"deleted_count": 0, "message": "No stale applications found"}

        deleted_count = 0
        errors = []

        for app in stale_apps:
            try:
                app.soft_delete()
                deleted_count += 1
                logger.info(f"[LOAN APPLICATION TASK] Soft-deleted stale application #{app.id}")
            except Exception as e:
                errors.append({"application_id": app.id, "error": str(e)})
                logger.error(f"[LOAN APPLICATION TASK] Failed to delete application #{app.id}: {e}")

        if deleted_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title="🧹 Stale Application Cleanup Completed",
                message=f"Deleted {deleted_count} stale applications, {len(errors)} errors.",
                type="info" if not errors else "error",
                metadata={"deleted_count": deleted_count, "errors": errors[:10]},
                user=user,
            )

        return {
            "deleted_count": deleted_count,
            "errors": errors,
            "message": f"Deleted {deleted_count} stale applications",
        }

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Stale cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task
def force_cleanup_stale(user: str = "system", days: int = 30):
    """Force immediate stale cleanup."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force stale cleanup triggered")
    return cleanup_stale_applications(days=days, user=user)