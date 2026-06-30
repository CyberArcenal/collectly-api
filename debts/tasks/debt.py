from decimal import Decimal
import logging
from django.utils import timezone
from celery import shared_task
from django.core.cache import cache

from debts.services.interest_accrual import InterestAccrualService
from debts.state_transitions.debt import DebtStateTransitionService
from notifications.services.notification import NotificationService

from datetime import datetime
from django.db import transaction

from debts.models.debt import Debt
from audit.utils.log import log_audit_event
from system_settings.utils.base import enable_auto_penalty

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_interest_accrual(self):
    """
    Celery task to run daily interest accrual for all eligible debts.

    This task runs on a schedule (default: daily at midnight) and accrues
    interest for all active and overdue debts based on their interest rates.

    Returns:
        dict: {
            'status': str,
            'processed': int,
            'errors': int,
            'message': str,
        }
    """
    logger.info("[INTEREST ACCRUAL] Starting daily interest accrual task...")

    try:
        # Run the accrual
        result = InterestAccrualService.run_daily_accrual()

        # Build response message
        message = (
            f"Interest accrual completed: {result['processed']} processed, "
            f"{result['errors']} errors, {result.get('skipped', 0)} skipped"
        )

        logger.info(f"[INTEREST ACCRUAL] {message}")

        # Send notification if there were errors
        if result["errors"] > 0:
            try:
                from notifications.services.notification import NotificationService

                NotificationService.notify_admins_and_staff(
                    title="⚠️ Interest Accrual Completed with Errors",
                    message=(
                        f'Interest accrual completed: {result["processed"]} processed, '
                        f'{result["errors"]} errors, {result.get("skipped", 0)} skipped. '
                        f"Please check logs for details."
                    ),
                    type="error",
                    metadata=result,
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[INTEREST ACCRUAL] Could not send notification: {e}")

        # Store last run stats in cache
        cache.set(
            "interest_accrual_last_run",
            {
                "timestamp": timezone.now().isoformat(),
                "processed": result["processed"],
                "errors": result["errors"],
                "skipped": result.get("skipped", 0),
                "total_interest": float(result.get("total_interest", 0)),
            },
            timeout=86400,  # 24 hours
        )

        # If there were errors, we might want to retry?
        # But we let the scheduler handle next run.
        # Only retry on actual exceptions.
        return {
            "status": "completed" if result["errors"] == 0 else "completed_with_errors",
            "processed": result["processed"],
            "errors": result["errors"],
            "skipped": result.get("skipped", 0),
            "total_interest": result.get("total_interest", 0),
            "message": message,
        }

    except Exception as e:
        logger.error(f"[INTEREST ACCRUAL] ❌ Error during interest accrual: {e}")

        # Send failure notification
        try:
            NotificationService.create(
                data={
                    "title": "❌ Interest Accrual Failed",
                    "message": f"Failed to run interest accrual: {str(e)}",
                    "type": "error",
                    "metadata": {"error": str(e)},
                },
                user="system",
                request=None,
            )
        except Exception as notif_err:
            logger.warning(
                f"[INTEREST ACCRUAL] Could not send failure notification: {notif_err}"
            )

        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task
def get_interest_accrual_stats():
    """
    Get statistics about interest accrual.

    Returns:
        dict: {
            'last_run': dict or None,
            'status': str,
            'enabled': bool,
        }
    """
    try:
        last_run = cache.get("interest_accrual_last_run")

        return {
            "last_run": last_run,
            "status": "active",
            "enabled": True,
        }
    except Exception as e:
        logger.error(f"[INTEREST ACCRUAL] Error getting stats: {e}")
        return {
            "error": str(e),
            "status": "error",
            "enabled": True,
        }


@shared_task
def force_interest_accrual():
    """
    Force immediate interest accrual run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[INTEREST ACCRUAL] 🔄 Force interest accrual triggered")
    return run_interest_accrual()


@shared_task
def accrue_interest_for_debt(debt_id, as_of_date=None):
    """
    Accrue interest for a specific debt.

    Args:
        debt_id: ID of the debt
        as_of_date: Optional date to accrue up to (YYYY-MM-DD or None for today)

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'message': str,
            'new_balance': float,
        }
    """
    try:
        from debts.models.debt import Debt
        from django.core.exceptions import ValidationError

        logger.info(f"[INTEREST ACCRUAL] Accruing interest for debt #{debt_id}")

        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt #{debt_id} not found",
            }

        # Parse date if provided
        if as_of_date:
            from datetime import datetime

            try:
                as_of_date = datetime.fromisoformat(as_of_date).date()
            except ValueError:
                return {
                    "debt_id": debt_id,
                    "success": False,
                    "message": f"Invalid date format: {as_of_date}",
                }

        # Run accrual for this debt
        updated_debt = InterestAccrualService.apply_accrual(debt, as_of_date)

        return {
            "debt_id": debt_id,
            "success": True,
            "message": f"Interest accrued successfully",
            "new_balance": float(updated_debt.remaining_amount),
            "last_accrual_date": (
                updated_debt.last_interest_accrual_date.isoformat()
                if updated_debt.last_interest_accrual_date
                else None
            ),
        }

    except Exception as e:
        logger.error(
            f"[INTEREST ACCRUAL] Error accruing interest for debt #{debt_id}: {e}"
        )
        return {
            "debt_id": debt_id,
            "success": False,
            "message": str(e),
        }


# Cache key for last run tracking
OVERDUE_CORRECTOR_LAST_RUN = "overdue_corrector_last_run"


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def correct_misoverdue_debts(self):
    """
    Celery task to correct misclassified overdue debts.

    This task runs on a schedule (default: daily at 1 AM) and scans for debts
    marked as 'overdue' that should actually be 'paid' or 'active'.

    Conditions for correction:
    1. Fully paid (remaining_amount <= 0.01) → status = 'paid'
    2. Due date is today or in the future → status = 'active'

    Returns:
        dict: {
            'status': str,
            'corrected': int,
            'message': str,
            'details': list,
        }
    """
    logger.info("[OVERDUE CORRECTOR] Starting overdue status correction task...")

    try:
        # Check if already ran today
        if _overdue_corrector_already_ran_today():
            logger.info("[OVERDUE CORRECTOR] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "corrected": 0,
                "details": [],
            }

        # Get all debts with status 'overdue' and not soft-deleted
        today = timezone.now().date()
        overdue_debts = Debt.objects.select_related("borrower").filter(
            status=Debt.Status.OVERDUE,
            deleted_at__isnull=True,
        )

        total_count = overdue_debts.count()
        logger.info(
            f"[OVERDUE CORRECTOR] Found {total_count} debts with status 'overdue'"
        )

        if total_count == 0:
            logger.info("[OVERDUE CORRECTOR] No overdue debts to check")
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

            # Case 1: Fully paid
            if remaining_balance <= 0.01:
                new_status = Debt.Status.PAID
                reason = "fully paid"

            # Case 2: Due date is today or in the future (not overdue anymore)
            elif due_date and due_date >= today:
                new_status = Debt.Status.ACTIVE
                reason = "due date extended or in the future"

            # Still overdue - no correction needed
            else:
                continue

            # Update the debt status
            with transaction.atomic():
                old_status = debt.status
                debt.status = new_status
                debt.updated_at = timezone.now()
                debt.save(update_fields=["status", "updated_at"])

                # Audit log
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
            correction_details.append(
                {
                    "debt_id": debt.id,
                    "debt_name": debt.name,
                    "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                    "old_status": old_status,
                    "new_status": new_status,
                    "reason": reason,
                    "remaining_balance": float(remaining_balance),
                    "due_date": due_date.isoformat() if due_date else None,
                }
            )

            logger.info(
                f"[OVERDUE CORRECTOR] Debt #{debt.id} status corrected: "
                f"overdue → {new_status} ({reason})"
            )

        # Send notification to admins/staff if corrections were made
        if corrected_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Overdue Status Corrector Completed",
                    message=(
                        f"Corrected {corrected_count} debt(s) with incorrect "
                        f'"overdue" status. Please review the details.'
                    ),
                    type="info",
                    metadata={
                        "corrected_count": corrected_count,
                        "details": correction_details[:10],  # First 10 only
                        "total_checked": total_count,
                    },
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[OVERDUE CORRECTOR] Could not send notification: {e}")

        # Mark as ran today
        _mark_overdue_corrector_ran_today()

        logger.info(
            f"[OVERDUE CORRECTOR] Completed: {corrected_count} corrected "
            f"out of {total_count} checked"
        )

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
            logger.warning(
                f"[OVERDUE CORRECTOR] Could not send failure notification: {notif_err}"
            )

        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task
def force_overdue_correction():
    """
    Force immediate overdue status correction run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[OVERDUE CORRECTOR] 🔄 Force overdue status correction triggered")
    return correct_misoverdue_debts()


@shared_task
def correct_specific_debt(debt_id):
    """
    Correct the status of a specific debt.

    Args:
        debt_id: ID of the debt to correct

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'old_status': str,
            'new_status': str,
            'message': str,
        }
    """
    try:
        debt = Debt.objects.filter(id=debt_id, deleted_at__isnull=True).first()
        if not debt:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": "Debt not found",
            }

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

        if remaining_balance <= 0.01:
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
        return {
            "debt_id": debt_id,
            "success": False,
            "message": str(e),
        }


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def _overdue_corrector_already_ran_today():
    """
    Check if the corrector task already ran today.

    Returns:
        bool: True if already ran today
    """
    last_run = cache.get(OVERDUE_CORRECTOR_LAST_RUN)
    if not last_run:
        return False

    last_run_date = last_run.get("date")
    if not last_run_date:
        return False

    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        today = timezone.now().date()
        return last_run_date == today
    except (ValueError, TypeError):
        return False


def _mark_overdue_corrector_ran_today():
    """
    Mark today as the last run date.
    """
    cache.set(
        OVERDUE_CORRECTOR_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,  # 2 days
    )


@shared_task
def get_overdue_corrector_status():
    """
    Get the status of the overdue corrector.

    Returns:
        dict: {
            'enabled': bool,
            'last_run': dict or None,
            'next_run': str or None,
            'is_running': bool,
        }
    """
    last_run = cache.get(OVERDUE_CORRECTOR_LAST_RUN)

    return {
        "enabled": True,
        "last_run": last_run,
        "is_running": True,
        "schedule": "Daily at 1:00 AM",
    }


# Cache key for last run tracking
OVERDUE_UPDATER_LAST_RUN = "overdue_updater_last_run"


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_overdue_statuses(self):
    """
    Celery task to update debt statuses to 'overdue' when due date has passed.

    This task runs on a schedule (default: daily at midnight) and finds active debts
    where due_date < today and remaining_amount > 0, then marks them as overdue.

    It triggers the DebtStateTransitionService.on_overdue which handles:
    - Status change to 'overdue'
    - Auto-penalty application (if enabled)
    - Notifications and email alerts

    Returns:
        dict: {
            'status': str,
            'updated': int,
            'message': str,
            'details': list,
        }
    """
    logger.info("[OVERDUE UPDATER] Starting overdue status update task...")

    try:
        # Check if already ran today
        if _overdue_updater_already_ran_today():
            logger.info("[OVERDUE UPDATER] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "updated": 0,
                "details": [],
            }

        # Get all active debts that are past due and have remaining balance
        today = timezone.now().date()
        debts_to_update = Debt.objects.select_related("borrower").filter(
            status=Debt.Status.ACTIVE,
            due_date__lt=today,
            remaining_amount__gt=0.01,
            deleted_at__isnull=True,
        )

        total_count = debts_to_update.count()
        logger.info(f"[OVERDUE UPDATER] Found {total_count} debts to mark as overdue")

        if total_count == 0:
            logger.info("[OVERDUE UPDATER] No debts need to be marked overdue")
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
                # Call the on_overdue transition
                # This will update status, apply penalties, send notifications
                result = transition_service.on_overdue(debt, user="system")

                updated_count += 1
                update_details.append(
                    {
                        "debt_id": debt.id,
                        "debt_name": debt.name,
                        "borrower_name": (
                            debt.borrower.name if debt.borrower else "Unknown"
                        ),
                        "due_date": debt.due_date.isoformat(),
                        "days_overdue": (today - debt.due_date).days,
                        "remaining_balance": float(debt.remaining_amount),
                        "penalty_applied": getattr(debt, "_penalty_applied", False),
                    }
                )

                logger.info(
                    f"[OVERDUE UPDATER] Debt #{debt.id} marked as overdue "
                    f"({(today - debt.due_date).days} days overdue)"
                )

            except Exception as e:
                failed_count += 1
                logger.error(f"[OVERDUE UPDATER] Failed to update debt #{debt.id}: {e}")

        # Send notification to admins/staff
        try:
            if updated_count > 0:
                NotificationService.notify_admins_and_staff(
                    title="⏰ Overdue Status Update Completed",
                    message=(
                        f"Marked {updated_count} debt(s) as overdue. "
                        f"{failed_count} failed."
                    ),
                    type="info",
                    metadata={
                        "updated": updated_count,
                        "failed": failed_count,
                        "total_checked": total_count,
                        "details": update_details[:10],  # First 10 only
                    },
                    user="system",
                )
        except Exception as e:
            logger.warning(f"[OVERDUE UPDATER] Could not send notification: {e}")

        # Mark as ran today
        _mark_overdue_updater_ran_today()

        logger.info(
            f"[OVERDUE UPDATER] Completed: {updated_count} updated, "
            f"{failed_count} failed out of {total_count} checked"
        )

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
            logger.warning(
                f"[OVERDUE UPDATER] Could not send failure notification: {notif_err}"
            )

        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task
def force_overdue_update():
    """
    Force immediate overdue status update run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[OVERDUE UPDATER] 🔄 Force overdue status update triggered")
    return update_overdue_statuses()


@shared_task
def update_specific_debt_status(debt_id):
    """
    Manually trigger overdue status update for a specific debt.

    Args:
        debt_id: ID of the debt to update

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'old_status': str,
            'new_status': str,
            'message': str,
            'days_overdue': int,
        }
    """
    try:
        debt = (
            Debt.objects.select_related("borrower")
            .filter(id=debt_id, deleted_at__isnull=True)
            .first()
        )

        if not debt:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": "Debt not found",
            }

        if debt.status != Debt.Status.ACTIVE:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt is not active (status: {debt.status})",
            }

        today = timezone.now().date()
        if debt.due_date >= today:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt is not overdue (due date: {debt.due_date})",
                "days_overdue": 0,
            }

        if debt.remaining_amount <= 0.01:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": "Debt is fully paid",
                "days_overdue": 0,
            }

        old_status = debt.status
        days_overdue = (today - debt.due_date).days

        # Call the transition service
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
        return {
            "debt_id": debt_id,
            "success": False,
            "message": str(e),
        }


@shared_task
def preview_overdue_update():
    """
    Preview which debts would be marked as overdue without actually updating them.

    Returns:
        dict: {
            'count': int,
            'debts': list,
        }
    """
    today = timezone.now().date()
    debts = Debt.objects.select_related("borrower").filter(
        status=Debt.Status.ACTIVE,
        due_date__lt=today,
        remaining_amount__gt=0.01,
        deleted_at__isnull=True,
    )

    preview_data = []
    for debt in debts:
        preview_data.append(
            {
                "debt_id": debt.id,
                "debt_name": debt.name,
                "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                "due_date": debt.due_date.isoformat(),
                "days_overdue": (today - debt.due_date).days,
                "remaining_balance": float(debt.remaining_amount),
                "total_amount": float(debt.total_amount),
            }
        )

    return {
        "count": len(preview_data),
        "debts": preview_data,
        "as_of_date": today.isoformat(),
    }


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def _overdue_updater_already_ran_today():
    """
    Check if the updater task already ran today.

    Returns:
        bool: True if already ran today
    """
    last_run = cache.get(OVERDUE_UPDATER_LAST_RUN)
    if not last_run:
        return False

    last_run_date = last_run.get("date")
    if not last_run_date:
        return False

    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        today = timezone.now().date()
        return last_run_date == today
    except (ValueError, TypeError):
        return False


def _mark_overdue_updater_ran_today():
    """
    Mark today as the last run date.
    """
    cache.set(
        OVERDUE_UPDATER_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,  # 2 days
    )


@shared_task
def get_overdue_updater_status():
    """
    Get the status of the overdue updater.

    Returns:
        dict: {
            'enabled': bool,
            'last_run': dict or None,
            'next_run': str or None,
            'is_running': bool,
            'schedule': str,
        }
    """
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
    """
    Health check task to verify overdue statuses are correct.
    Checks for inconsistencies like:
    - Debts marked as overdue but due date is in the future
    - Debts marked as active but due date is past due

    Returns:
        dict: {
            'issues_found': int,
            'issues': list,
        }
    """
    today = timezone.now().date()
    issues = []

    # Check 1: Debts marked as overdue but due_date is today or in future
    false_overdue = Debt.objects.filter(
        status=Debt.Status.OVERDUE,
        due_date__gte=today,
        deleted_at__isnull=True,
    )

    for debt in false_overdue:
        issues.append(
            {
                "type": "false_overdue",
                "debt_id": debt.id,
                "debt_name": debt.name,
                "due_date": debt.due_date.isoformat(),
                "status": debt.status,
                "message": f"Debt marked as overdue but due date is {debt.due_date}",
            }
        )

    # Check 2: Debts marked as active but due_date is past due and remaining > 0
    missed_overdue = Debt.objects.filter(
        status=Debt.Status.ACTIVE,
        due_date__lt=today,
        remaining_amount__gt=0.01,
        deleted_at__isnull=True,
    )

    for debt in missed_overdue:
        issues.append(
            {
                "type": "missed_overdue",
                "debt_id": debt.id,
                "debt_name": debt.name,
                "due_date": debt.due_date.isoformat(),
                "days_overdue": (today - debt.due_date).days,
                "status": debt.status,
                "message": f"Debt is { (today - debt.due_date).days } days overdue but still active",
            }
        )

    # Check 3: Debts marked as overdue but remaining <= 0
    paid_overdue = Debt.objects.filter(
        status=Debt.Status.OVERDUE,
        remaining_amount__lte=0.01,
        deleted_at__isnull=True,
    )

    for debt in paid_overdue:
        issues.append(
            {
                "type": "paid_overdue",
                "debt_id": debt.id,
                "debt_name": debt.name,
                "remaining_balance": float(debt.remaining_amount),
                "status": debt.status,
                "message": "Debt marked as overdue but fully paid",
            }
        )

    # Send alert if issues found
    if issues:
        try:
            NotificationService.notify_admins_and_staff(
                title="⚠️ Overdue Status Health Check Issues Found",
                message=(
                    f"Found {len(issues)} issues in overdue statuses. "
                    f"Please review the details."
                ),
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
            logger.warning(
                f"[OVERDUE UPDATER] Could not send health check notification: {e}"
            )

    return {
        "issues_found": len(issues),
        "false_overdue_count": false_overdue.count(),
        "missed_overdue_count": missed_overdue.count(),
        "paid_overdue_count": paid_overdue.count(),
        "issues": issues[:20],  # First 20 issues
    }


# Cache key for last run tracking
ZERO_BALANCE_FIXER_LAST_RUN = "zero_balance_fixer_last_run"


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def fix_zero_balance_debts(self):
    """
    Celery task to fix debts with zero remaining balance but incorrect status.

    This task runs on a schedule (default: daily at 4 AM) and finds debts
    where remaining_amount <= 0 but status is not 'paid', then corrects them.

    Returns:
        dict: {
            'status': str,
            'fixed': int,
            'message': str,
            'details': list,
        }
    """
    logger.info("[ZERO BALANCE FIXER] Starting zero balance debt fixer task...")

    try:
        # Check if already ran today
        if _zero_balance_already_ran_today():
            logger.info("[ZERO BALANCE FIXER] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "fixed": 0,
                "details": [],
            }

        # Find debts with zero remaining balance but status is NOT 'paid'
        debts_to_fix = (
            Debt.objects.select_related("borrower")
            .filter(
                remaining_amount__lte=Decimal("0.01"),
                deleted_at__isnull=True,
            )
            .exclude(status=Debt.Status.PAID)
        )

        total_count = debts_to_fix.count()
        logger.info(
            f"[ZERO BALANCE FIXER] Found {total_count} debts with zero balance and incorrect status"
        )

        if total_count == 0:
            logger.info("[ZERO BALANCE FIXER] No debts to fix")
            _mark_zero_balance_ran_today()
            return {
                "status": "completed",
                "message": "No debts to fix",
                "fixed": 0,
                "details": [],
            }

        fixed_count = 0
        fix_details = []

        for debt in debts_to_fix:
            with transaction.atomic():
                old_status = debt.status
                old_remaining = debt.remaining_amount

                # Update status to 'paid'
                debt.status = Debt.Status.PAID
                debt.updated_at = timezone.now()
                debt.save(update_fields=["status", "updated_at"])

                # Audit log
                log_audit_event(
                    request=None,
                    user="system",
                    action_type="status_change",
                    model_name="Debt",
                    object_id=str(debt.id),
                    changes={
                        "before": {
                            "status": old_status,
                            "remaining_amount": float(old_remaining),
                        },
                        "after": {
                            "status": Debt.Status.PAID,
                            "remaining_amount": float(debt.remaining_amount),
                        },
                        "reason": "Auto-corrected because remaining amount is zero",
                        "corrector": "zero_balance_fixer",
                    },
                )

                fixed_count += 1
                fix_details.append(
                    {
                        "debt_id": debt.id,
                        "debt_name": debt.name,
                        "borrower_name": (
                            debt.borrower.name if debt.borrower else "Unknown"
                        ),
                        "old_status": old_status,
                        "new_status": Debt.Status.PAID,
                        "remaining_balance": float(old_remaining),
                    }
                )

                logger.info(
                    f"[ZERO BALANCE FIXER] Debt #{debt.id} fixed: "
                    f"{old_status} → paid (remaining: {old_remaining})"
                )

        # Send notification to admins/staff
        if fixed_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Zero Balance Debts Fixed",
                    message=(
                        f"Fixed {fixed_count} debt(s) with zero remaining balance "
                        f"that were not marked as paid."
                    ),
                    type="info",
                    metadata={
                        "fixed_count": fixed_count,
                        "total_checked": total_count,
                        "details": fix_details[:10],
                    },
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[ZERO BALANCE FIXER] Could not send notification: {e}")

        # Mark as ran today
        _mark_zero_balance_ran_today()

        logger.info(f"[ZERO BALANCE FIXER] Completed: {fixed_count} debts fixed")

        return {
            "status": "completed",
            "fixed": fixed_count,
            "total_checked": total_count,
            "message": f"Fixed {fixed_count} debts with zero balance",
            "details": fix_details,
        }

    except Exception as e:
        logger.error(f"[ZERO BALANCE FIXER] ❌ Error during fix: {e}")

        try:
            NotificationService.notify_admins_and_staff(
                title="❌ Zero Balance Fixer Failed",
                message=f"Failed to fix zero balance debts: {str(e)}",
                type="error",
                metadata={"error": str(e)},
                user="system",
            )
        except Exception as notif_err:
            logger.warning(
                f"[ZERO BALANCE FIXER] Could not send failure notification: {notif_err}"
            )

        raise self.retry(exc=e, countdown=300 * (2**self.request.retries))


@shared_task
def force_zero_balance_fix():
    """
    Force immediate zero balance fix run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[ZERO BALANCE FIXER] 🔄 Force zero balance fix triggered")
    return fix_zero_balance_debts()


@shared_task
def fix_specific_debt_zero_balance(debt_id):
    """
    Manually fix a specific debt with zero balance.

    Args:
        debt_id: ID of the debt to fix

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'old_status': str,
            'new_status': str,
            'message': str,
        }
    """
    try:
        debt = (
            Debt.objects.select_related("borrower")
            .filter(id=debt_id, deleted_at__isnull=True)
            .first()
        )

        if not debt:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": "Debt not found",
            }

        if debt.remaining_amount > Decimal("0.01"):
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt has remaining balance of {debt.remaining_amount}",
                "remaining_balance": float(debt.remaining_amount),
            }

        if debt.status == Debt.Status.PAID:
            return {
                "debt_id": debt_id,
                "success": True,
                "message": "Debt is already marked as paid",
                "old_status": debt.status,
                "new_status": debt.status,
            }

        old_status = debt.status
        old_remaining = debt.remaining_amount

        with transaction.atomic():
            debt.status = Debt.Status.PAID
            debt.updated_at = timezone.now()
            debt.save(update_fields=["status", "updated_at"])

            log_audit_event(
                request=None,
                user="system",
                action_type="status_change",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": {
                        "status": old_status,
                        "remaining_amount": float(old_remaining),
                    },
                    "after": {
                        "status": Debt.Status.PAID,
                        "remaining_amount": float(debt.remaining_amount),
                    },
                    "reason": "Manually fixed because remaining amount is zero",
                    "corrector": "manual",
                },
            )

        return {
            "debt_id": debt_id,
            "success": True,
            "old_status": old_status,
            "new_status": Debt.Status.PAID,
            "message": f"Debt fixed: {old_status} → paid",
            "remaining_balance": float(old_remaining),
        }

    except Exception as e:
        logger.error(f"[ZERO BALANCE FIXER] Error fixing debt #{debt_id}: {e}")
        return {
            "debt_id": debt_id,
            "success": False,
            "message": str(e),
        }


@shared_task
def preview_zero_balance_debts():
    """
    Preview which debts would be fixed without actually updating them.

    Returns:
        dict: {
            'count': int,
            'debts': list,
            'as_of_date': str,
        }
    """
    debts = (
        Debt.objects.select_related("borrower")
        .filter(
            remaining_amount__lte=Decimal("0.01"),
            deleted_at__isnull=True,
        )
        .exclude(status=Debt.Status.PAID)
    )

    preview_data = []
    for debt in debts:
        preview_data.append(
            {
                "debt_id": debt.id,
                "debt_name": debt.name,
                "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                "status": debt.status,
                "remaining_balance": float(debt.remaining_amount),
                "total_amount": float(debt.total_amount),
            }
        )

    return {
        "count": len(preview_data),
        "debts": preview_data,
        "as_of_date": timezone.now().date().isoformat(),
    }


@shared_task
def check_zero_balance_health():
    """
    Health check task to verify zero balance debts are properly marked as paid.

    Finds debts with:
    - remaining_amount <= 0 but status != 'paid' (should be fixed)
    - remaining_amount > 0 but status == 'paid' (inconsistent - should be investigated)

    Returns:
        dict: {
            'issues_found': int,
            'issues': list,
        }
    """
    issues = []

    # Issue 1: Zero balance but not paid
    zero_balance_not_paid = (
        Debt.objects.select_related("borrower")
        .filter(
            remaining_amount__lte=Decimal("0.01"),
            deleted_at__isnull=True,
        )
        .exclude(status=Debt.Status.PAID)
    )

    for debt in zero_balance_not_paid:
        issues.append(
            {
                "type": "zero_balance_not_paid",
                "debt_id": debt.id,
                "debt_name": debt.name,
                "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                "status": debt.status,
                "remaining_balance": float(debt.remaining_amount),
                "message": f'Debt has zero remaining balance but status is "{debt.status}"',
            }
        )

    # Issue 2: Paid status but positive balance
    paid_with_balance = Debt.objects.select_related("borrower").filter(
        remaining_amount__gt=Decimal("0.01"),
        status=Debt.Status.PAID,
        deleted_at__isnull=True,
    )

    for debt in paid_with_balance:
        issues.append(
            {
                "type": "paid_with_balance",
                "debt_id": debt.id,
                "debt_name": debt.name,
                "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                "status": debt.status,
                "remaining_balance": float(debt.remaining_amount),
                "message": f"Debt is marked as paid but has remaining balance of {debt.remaining_amount}",
            }
        )

    # Send alert if issues found
    if issues:
        try:
            NotificationService.notify_admins_and_staff(
                title="⚠️ Zero Balance Health Check Issues Found",
                message=f"Found {len(issues)} issues with zero balance debts.",
                type="error",
                metadata={
                    "issues_found": len(issues),
                    "zero_balance_not_paid": zero_balance_not_paid.count(),
                    "paid_with_balance": paid_with_balance.count(),
                },
                user="system",
            )
        except Exception as e:
            logger.warning(
                f"[ZERO BALANCE FIXER] Could not send health check notification: {e}"
            )

    return {
        "issues_found": len(issues),
        "zero_balance_not_paid_count": zero_balance_not_paid.count(),
        "paid_with_balance_count": paid_with_balance.count(),
        "issues": issues[:20],
    }


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def _zero_balance_already_ran_today():
    """
    Check if the fixer task already ran today.

    Returns:
        bool: True if already ran today
    """
    last_run = cache.get(ZERO_BALANCE_FIXER_LAST_RUN)
    if not last_run:
        return False

    last_run_date = last_run.get("date")
    if not last_run_date:
        return False

    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        today = timezone.now().date()
        return last_run_date == today
    except (ValueError, TypeError):
        return False


def _mark_zero_balance_ran_today():
    """
    Mark today as the last run date.
    """
    cache.set(
        ZERO_BALANCE_FIXER_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,  # 2 days
    )


@shared_task
def get_zero_balance_fixer_status():
    """
    Get the status of the zero balance fixer.

    Returns:
        dict: {
            'enabled': bool,
            'last_run': dict or None,
            'is_running': bool,
            'schedule': str,
        }
    """
    last_run = cache.get(ZERO_BALANCE_FIXER_LAST_RUN)

    return {
        "enabled": True,
        "last_run": last_run,
        "is_running": True,
        "schedule": "Daily at 4:00 AM",
    }
