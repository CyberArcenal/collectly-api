# debts/tasks/overdue_update.py
from decimal import Decimal
import logging
from django.utils import timezone
from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from datetime import datetime

from debts.models.debt import Debt
from debts.state_transitions.debt import DebtStateTransitionService
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event
from system_settings.utils.base import enable_auto_penalty

logger = logging.getLogger(__name__)

OVERDUE_UPDATER_LAST_RUN = "overdue_updater_last_run"


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_overdue_statuses(self):
    """
    Mark active debts as overdue when due date has passed.
    """
    logger.info("[OVERDUE UPDATER] Starting overdue status update task...")
    try:
        if _overdue_updater_already_ran_today():
            logger.info("[OVERDUE UPDATER] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "updated": 0,
                "details": [],
            }

        today = timezone.now().date()
        debts_to_update = Debt.objects.select_related("borrower").filter(
            status=Debt.Status.ACTIVE,
            due_date__lt=today,
            remaining_amount__gt=Decimal("0.01"),
            deleted_at__isnull=True,
        )

        total_count = debts_to_update.count()
        logger.info(f"[OVERDUE UPDATER] Found {total_count} debts to mark as overdue")

        if total_count == 0:
            _mark_overdue_updater_ran_today()
            return {
                "status": "completed",
                "message": "No debts need to be marked overdue",
                "updated": 0,
                "details": [],
            }

        updated_count = 0
        failed_count = 0
        update_details = []
        transition_service = DebtStateTransitionService()

        for debt in debts_to_update:
            try:
                result = transition_service.on_overdue(debt, user="system")
                updated_count += 1
                update_details.append({
                    "debt_id": debt.id,
                    "debt_name": debt.name,
                    "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                    "due_date": debt.due_date.isoformat(),
                    "days_overdue": (today - debt.due_date).days,
                    "remaining_balance": float(debt.remaining_amount),
                    "penalty_applied": getattr(debt, "_penalty_applied", False),
                })
                logger.info(f"[OVERDUE UPDATER] Debt #{debt.id} marked as overdue ({(today - debt.due_date).days} days overdue)")
            except Exception as e:
                failed_count += 1
                logger.error(f"[OVERDUE UPDATER] Failed to update debt #{debt.id}: {e}")

        if updated_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="⏰ Overdue Status Update Completed",
                    message=f"Marked {updated_count} debt(s) as overdue. {failed_count} failed.",
                    type="info",
                    metadata={
                        "updated": updated_count,
                        "failed": failed_count,
                        "total_checked": total_count,
                        "details": update_details[:10],
                    },
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[OVERDUE UPDATER] Could not send notification: {e}")

        _mark_overdue_updater_ran_today()
        logger.info(f"[OVERDUE UPDATER] Completed: {updated_count} updated, {failed_count} failed out of {total_count} checked")
        return {
            "status": "completed" if failed_count == 0 else "completed_with_failures",
            "updated": updated_count,
            "failed": failed_count,
            "total_checked": total_count,
            "message": f"Updated {updated_count} debts as overdue ({failed_count} failed)",
            "details": update_details,
        }
    except Exception as e:
        logger.error(f"[OVERDUE UPDATER] ❌ Error during update: {e}")
        try:
            NotificationService.notify_admins_and_staff(
                title="❌ Overdue Status Update Failed",
                message=f"Failed to update overdue statuses: {str(e)}",
                type="error",
                metadata={"error": str(e)},
                user="system",
            )
        except Exception as notif_err:
            logger.warning(f"[OVERDUE UPDATER] Could not send failure notification: {notif_err}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_overdue_update():
    """Force immediate overdue status update."""
    logger.info("[OVERDUE UPDATER] 🔄 Force overdue status update triggered")
    return update_overdue_statuses()


@shared_task
def update_specific_debt_status(debt_id):
    """Manually mark a specific debt as overdue."""
    try:
        debt = Debt.objects.select_related("borrower").filter(id=debt_id, deleted_at__isnull=True).first()
        if not debt:
            return {"debt_id": debt_id, "success": False, "message": "Debt not found"}
        if debt.status != Debt.Status.ACTIVE:
            return {"debt_id": debt_id, "success": False, "message": f"Debt is not active (status: {debt.status})"}

        today = timezone.now().date()
        if debt.due_date >= today:
            return {"debt_id": debt_id, "success": False, "message": f"Debt is not overdue (due date: {debt.due_date})", "days_overdue": 0}
        if debt.remaining_amount <= Decimal("0.01"):
            return {"debt_id": debt_id, "success": False, "message": "Debt is fully paid", "days_overdue": 0}

        old_status = debt.status
        days_overdue = (today - debt.due_date).days
        transition_service = DebtStateTransitionService()
        transition_service.on_overdue(debt, user="system")
        return {
            "debt_id": debt_id,
            "success": True,
            "old_status": old_status,
            "new_status": Debt.Status.OVERDUE,
            "days_overdue": days_overdue,
            "message": f"Debt marked as overdue ({days_overdue} days overdue)",
        }
    except Exception as e:
        logger.error(f"[OVERDUE UPDATER] Error updating debt #{debt_id}: {e}")
        return {"debt_id": debt_id, "success": False, "message": str(e)}


@shared_task
def preview_overdue_update():
    """Preview which debts would be marked as overdue."""
    today = timezone.now().date()
    debts = Debt.objects.select_related("borrower").filter(
        status=Debt.Status.ACTIVE,
        due_date__lt=today,
        remaining_amount__gt=Decimal("0.01"),
        deleted_at__isnull=True,
    )
    preview_data = []
    for debt in debts:
        preview_data.append({
            "debt_id": debt.id,
            "debt_name": debt.name,
            "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
            "due_date": debt.due_date.isoformat(),
            "days_overdue": (today - debt.due_date).days,
            "remaining_balance": float(debt.remaining_amount),
            "total_amount": float(debt.total_amount),
        })
    return {
        "count": len(preview_data),
        "debts": preview_data,
        "as_of_date": today.isoformat(),
    }


@shared_task
def get_overdue_updater_status():
    """Get status of the overdue updater."""
    last_run = cache.get(OVERDUE_UPDATER_LAST_RUN)
    return {
        "enabled": True,
        "last_run": last_run,
        "is_running": True,
        "schedule": "Daily at 12:00 AM",
        "auto_penalty_enabled": enable_auto_penalty(),
    }


@shared_task
def check_overdue_status_health():
    """Health check for overdue status inconsistencies."""
    today = timezone.now().date()
    issues = []

    # False overdue
    false_overdue = Debt.objects.filter(
        status=Debt.Status.OVERDUE,
        due_date__gte=today,
        deleted_at__isnull=True,
    )
    for debt in false_overdue:
        issues.append({
            "type": "false_overdue",
            "debt_id": debt.id,
            "debt_name": debt.name,
            "due_date": debt.due_date.isoformat(),
            "status": debt.status,
            "message": f"Debt marked as overdue but due date is {debt.due_date}",
        })

    # Missed overdue
    missed_overdue = Debt.objects.filter(
        status=Debt.Status.ACTIVE,
        due_date__lt=today,
        remaining_amount__gt=Decimal("0.01"),
        deleted_at__isnull=True,
    )
    for debt in missed_overdue:
        issues.append({
            "type": "missed_overdue",
            "debt_id": debt.id,
            "debt_name": debt.name,
            "due_date": debt.due_date.isoformat(),
            "days_overdue": (today - debt.due_date).days,
            "status": debt.status,
            "message": f"Debt is {(today - debt.due_date).days} days overdue but still active",
        })

    # Paid overdue
    paid_overdue = Debt.objects.filter(
        status=Debt.Status.OVERDUE,
        remaining_amount__lte=Decimal("0.01"),
        deleted_at__isnull=True,
    )
    for debt in paid_overdue:
        issues.append({
            "type": "paid_overdue",
            "debt_id": debt.id,
            "debt_name": debt.name,
            "remaining_balance": float(debt.remaining_amount),
            "status": debt.status,
            "message": "Debt marked as overdue but fully paid",
        })

    if issues:
        try:
            NotificationService.notify_admins_and_staff(
                title="⚠️ Overdue Status Health Check Issues Found",
                message=f"Found {len(issues)} issues in overdue statuses.",
                type="error",
                metadata={
                    "issues_found": len(issues),
                    "false_overdue": false_overdue.count(),
                    "missed_overdue": missed_overdue.count(),
                    "paid_overdue": paid_overdue.count(),
                },
                user="system",
            )
        except Exception as e:
            logger.warning(f"[OVERDUE UPDATER] Could not send health check notification: {e}")

    return {
        "issues_found": len(issues),
        "false_overdue_count": false_overdue.count(),
        "missed_overdue_count": missed_overdue.count(),
        "paid_overdue_count": paid_overdue.count(),
        "issues": issues[:20],
    }


# Helper functions
def _overdue_updater_already_ran_today():
    last_run = cache.get(OVERDUE_UPDATER_LAST_RUN)
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


def _mark_overdue_updater_ran_today():
    cache.set(
        OVERDUE_UPDATER_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,
    )