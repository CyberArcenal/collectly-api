# debts/tasks/overdue_correction.py
from decimal import Decimal
import logging
from django.utils import timezone
from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from datetime import datetime

from debts.models.debt import Debt
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)

OVERDUE_CORRECTOR_LAST_RUN = "overdue_corrector_last_run"


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def correct_misoverdue_debts(self):
    """
    Correct misclassified overdue debts.
    """
    logger.info("[OVERDUE CORRECTOR] Starting overdue status correction task...")
    try:
        if _overdue_corrector_already_ran_today():
            logger.info("[OVERDUE CORRECTOR] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "corrected": 0,
                "details": [],
            }

        today = timezone.now().date()
        overdue_debts = Debt.objects.select_related("borrower").filter(
            status=Debt.Status.OVERDUE,
            deleted_at__isnull=True,
        )
        total_count = overdue_debts.count()
        logger.info(f"[OVERDUE CORRECTOR] Found {total_count} debts with status 'overdue'")

        if total_count == 0:
            _mark_overdue_corrector_ran_today()
            return {
                "status": "completed",
                "message": "No overdue debts to check",
                "corrected": 0,
                "details": [],
            }

        corrected_count = 0
        correction_details = []

        for debt in overdue_debts:
            remaining_balance = debt.remaining_amount
            due_date = debt.due_date
            new_status = None
            reason = None

            if remaining_balance <= Decimal("0.01"):
                new_status = Debt.Status.PAID
                reason = "fully paid"
            elif due_date and due_date >= today:
                new_status = Debt.Status.ACTIVE
                reason = "due date extended or in the future"
            else:
                continue

            with transaction.atomic():
                old_status = debt.status
                debt.status = new_status
                debt.updated_at = timezone.now()
                debt.save(update_fields=["status", "updated_at"])
                log_audit_event(
                    request=None,
                    user="system",
                    action_type="status_change",
                    model_name="Debt",
                    object_id=str(debt.id),
                    changes={
                        "before": {"status": old_status},
                        "after": {"status": new_status},
                        "reason": reason,
                        "corrector": "overdue_status_corrector",
                    },
                )

            corrected_count += 1
            correction_details.append({
                "debt_id": debt.id,
                "debt_name": debt.name,
                "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
                "remaining_balance": float(remaining_balance),
                "due_date": due_date.isoformat() if due_date else None,
            })
            logger.info(f"[OVERDUE CORRECTOR] Debt #{debt.id} status corrected: overdue → {new_status} ({reason})")

        if corrected_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Overdue Status Corrector Completed",
                    message=f"Corrected {corrected_count} debt(s) with incorrect 'overdue' status.",
                    type="info",
                    metadata={
                        "corrected_count": corrected_count,
                        "details": correction_details[:10],
                        "total_checked": total_count,
                    },
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[OVERDUE CORRECTOR] Could not send notification: {e}")

        _mark_overdue_corrector_ran_today()
        logger.info(f"[OVERDUE CORRECTOR] Completed: {corrected_count} corrected out of {total_count} checked")
        return {
            "status": "completed",
            "corrected": corrected_count,
            "total_checked": total_count,
            "message": f"Corrected {corrected_count} out of {total_count} debts",
            "details": correction_details,
        }
    except Exception as e:
        logger.error(f"[OVERDUE CORRECTOR] ❌ Error during correction: {e}")
        try:
            NotificationService.notify_admins_and_staff(
                title="❌ Overdue Status Corrector Failed",
                message=f"Failed to correct overdue debts: {str(e)}",
                type="error",
                metadata={"error": str(e)},
                user="system",
            )
        except Exception as notif_err:
            logger.warning(f"[OVERDUE CORRECTOR] Could not send failure notification: {notif_err}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_overdue_correction():
    """
    Force immediate overdue status correction.
    """
    logger.info("[OVERDUE CORRECTOR] 🔄 Force overdue status correction triggered")
    return correct_misoverdue_debts()


@shared_task
def correct_specific_debt(debt_id):
    """
    Correct the status of a specific debt.
    """
    try:
        debt = Debt.objects.filter(id=debt_id, deleted_at__isnull=True).first()
        if not debt:
            return {"debt_id": debt_id, "success": False, "message": "Debt not found"}
        if debt.status != Debt.Status.OVERDUE:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt is not overdue (status: {debt.status})",
            }

        today = timezone.now().date()
        remaining_balance = debt.remaining_amount
        due_date = debt.due_date
        new_status = None
        reason = None

        if remaining_balance <= Decimal("0.01"):
            new_status = Debt.Status.PAID
            reason = "fully paid"
        elif due_date and due_date >= today:
            new_status = Debt.Status.ACTIVE
            reason = "due date is today or in the future"
        else:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": "Debt is still overdue (no correction needed)",
                "days_overdue": (today - due_date).days if due_date else None,
            }

        old_status = debt.status
        debt.status = new_status
        debt.updated_at = timezone.now()
        debt.save(update_fields=["status", "updated_at"])

        log_audit_event(
            request=None,
            user="system",
            action_type="status_change",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "before": {"status": old_status},
                "after": {"status": new_status},
                "reason": reason,
                "corrector": "manual",
            },
        )
        return {
            "debt_id": debt_id,
            "success": True,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
            "message": f"Debt corrected: {old_status} → {new_status} ({reason})",
        }
    except Exception as e:
        logger.error(f"[OVERDUE CORRECTOR] Error correcting debt #{debt_id}: {e}")
        return {"debt_id": debt_id, "success": False, "message": str(e)}


@shared_task
def get_overdue_corrector_status():
    """
    Get the status of the overdue corrector.
    """
    last_run = cache.get(OVERDUE_CORRECTOR_LAST_RUN)
    return {
        "enabled": True,
        "last_run": last_run,
        "is_running": True,
        "schedule": "Daily at 1:00 AM",
    }


# Helper functions
def _overdue_corrector_already_ran_today():
    last_run = cache.get(OVERDUE_CORRECTOR_LAST_RUN)
    if not last_run:
        return False
    last_run_date = last_run.get("date")
    if not last_run_date:
        return False
    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        return last_run_date == timezone.now().date()
    except (ValueError, TypeError):
        return False


def _mark_overdue_corrector_ran_today():
    cache.set(
        OVERDUE_CORRECTOR_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,
    )