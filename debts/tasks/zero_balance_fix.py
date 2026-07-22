# debts/tasks/zero_balance_fix.py
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

ZERO_BALANCE_FIXER_LAST_RUN = "zero_balance_fixer_last_run"


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def fix_zero_balance_debts(self):
    """
    Fix debts with zero remaining balance but incorrect status.
    """
    logger.info("[ZERO BALANCE FIXER] Starting zero balance debt fixer task...")
    try:
        if _zero_balance_already_ran_today():
            logger.info("[ZERO BALANCE FIXER] Already ran today, skipping")
            return {
                "status": "skipped",
                "message": "Already ran today",
                "fixed": 0,
                "details": [],
            }

        debts_to_fix = Debt.objects.select_related("borrower").filter(
            remaining_amount__lte=Decimal("0.01"),
            deleted_at__isnull=True,
        ).exclude(status=Debt.Status.PAID)

        total_count = debts_to_fix.count()
        logger.info(f"[ZERO BALANCE FIXER] Found {total_count} debts with zero balance and incorrect status")

        if total_count == 0:
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
                        "before": {"status": old_status, "remaining_amount": float(old_remaining)},
                        "after": {"status": Debt.Status.PAID, "remaining_amount": float(debt.remaining_amount)},
                        "reason": "Auto-corrected because remaining amount is zero",
                        "corrector": "zero_balance_fixer",
                    },
                )
                fixed_count += 1
                fix_details.append({
                    "debt_id": debt.id,
                    "debt_name": debt.name,
                    "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
                    "old_status": old_status,
                    "new_status": Debt.Status.PAID,
                    "remaining_balance": float(old_remaining),
                })
                logger.info(f"[ZERO BALANCE FIXER] Debt #{debt.id} fixed: {old_status} → paid (remaining: {old_remaining})")

        if fixed_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="🔄 Zero Balance Debts Fixed",
                    message=f"Fixed {fixed_count} debt(s) with zero remaining balance.",
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
            logger.warning(f"[ZERO BALANCE FIXER] Could not send failure notification: {notif_err}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_zero_balance_fix():
    """Force immediate zero balance fix run."""
    logger.info("[ZERO BALANCE FIXER] 🔄 Force zero balance fix triggered")
    return fix_zero_balance_debts()


@shared_task
def fix_specific_debt_zero_balance(debt_id):
    """Manually fix a specific debt with zero balance."""
    try:
        debt = Debt.objects.select_related("borrower").filter(id=debt_id, deleted_at__isnull=True).first()
        if not debt:
            return {"debt_id": debt_id, "success": False, "message": "Debt not found"}
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
                    "before": {"status": old_status, "remaining_amount": float(old_remaining)},
                    "after": {"status": Debt.Status.PAID, "remaining_amount": float(debt.remaining_amount)},
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
        return {"debt_id": debt_id, "success": False, "message": str(e)}


@shared_task
def preview_zero_balance_debts():
    """Preview which debts would be fixed without updating."""
    debts = Debt.objects.select_related("borrower").filter(
        remaining_amount__lte=Decimal("0.01"),
        deleted_at__isnull=True,
    ).exclude(status=Debt.Status.PAID)

    preview_data = []
    for debt in debts:
        preview_data.append({
            "debt_id": debt.id,
            "debt_name": debt.name,
            "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
            "status": debt.status,
            "remaining_balance": float(debt.remaining_amount),
            "total_amount": float(debt.total_amount),
        })
    return {
        "count": len(preview_data),
        "debts": preview_data,
        "as_of_date": timezone.now().date().isoformat(),
    }


@shared_task
def check_zero_balance_health():
    """Health check for zero balance debt inconsistencies."""
    issues = []

    zero_balance_not_paid = Debt.objects.select_related("borrower").filter(
        remaining_amount__lte=Decimal("0.01"),
        deleted_at__isnull=True,
    ).exclude(status=Debt.Status.PAID)

    for debt in zero_balance_not_paid:
        issues.append({
            "type": "zero_balance_not_paid",
            "debt_id": debt.id,
            "debt_name": debt.name,
            "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
            "status": debt.status,
            "remaining_balance": float(debt.remaining_amount),
            "message": f'Debt has zero remaining balance but status is "{debt.status}"',
        })

    paid_with_balance = Debt.objects.select_related("borrower").filter(
        remaining_amount__gt=Decimal("0.01"),
        status=Debt.Status.PAID,
        deleted_at__isnull=True,
    )

    for debt in paid_with_balance:
        issues.append({
            "type": "paid_with_balance",
            "debt_id": debt.id,
            "debt_name": debt.name,
            "borrower_name": debt.borrower.name if debt.borrower else "Unknown",
            "status": debt.status,
            "remaining_balance": float(debt.remaining_amount),
            "message": f"Debt is marked as paid but has remaining balance of {debt.remaining_amount}",
        })

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
            logger.warning(f"[ZERO BALANCE FIXER] Could not send health check notification: {e}")

    return {
        "issues_found": len(issues),
        "zero_balance_not_paid_count": zero_balance_not_paid.count(),
        "paid_with_balance_count": paid_with_balance.count(),
        "issues": issues[:20],
    }


# Helper functions
def _zero_balance_already_ran_today():
    last_run = cache.get(ZERO_BALANCE_FIXER_LAST_RUN)
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


def _mark_zero_balance_ran_today():
    cache.set(
        ZERO_BALANCE_FIXER_LAST_RUN,
        {
            "date": timezone.now().isoformat(),
            "timestamp": timezone.now().isoformat(),
        },
        timeout=86400 * 2,
    )


@shared_task
def get_zero_balance_fixer_status():
    """Get status of the zero balance fixer."""
    last_run = cache.get(ZERO_BALANCE_FIXER_LAST_RUN)
    return {
        "enabled": True,
        "last_run": last_run,
        "is_running": True,
        "schedule": "Daily at 4:00 AM",
    }