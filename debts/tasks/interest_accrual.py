# debts/tasks/interest_accrual.py
from decimal import Decimal
import logging
from django.utils import timezone
from celery import shared_task
from django.core.cache import cache
from django.db import transaction

from debts.services.interest_accrual import InterestAccrualService
from notifications.services.notification import NotificationService
from debts.models.debt import Debt
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_interest_accrual(self):
    """
    Celery task to run daily interest accrual for all eligible debts.
    """
    logger.info("[INTEREST ACCRUAL] Starting daily interest accrual task...")
    try:
        result = InterestAccrualService.run_daily_accrual()
        message = (
            f"Interest accrual completed: {result['processed']} processed, "
            f"{result['errors']} errors, {result.get('skipped', 0)} skipped"
        )
        logger.info(f"[INTEREST ACCRUAL] {message}")

        if result["errors"] > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title="⚠️ Interest Accrual Completed with Errors",
                    message=message,
                    type="error",
                    metadata=result,
                    user="system",
                )
            except Exception as e:
                logger.warning(f"[INTEREST ACCRUAL] Could not send notification: {e}")

        cache.set(
            "interest_accrual_last_run",
            {
                "timestamp": timezone.now().isoformat(),
                "processed": result["processed"],
                "errors": result["errors"],
                "skipped": result.get("skipped", 0),
                "total_interest": float(result.get("total_interest", 0)),
            },
            timeout=86400,
        )

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
            logger.warning(f"[INTEREST ACCRUAL] Could not send failure notification: {notif_err}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def get_interest_accrual_stats():
    """
    Get statistics about interest accrual.
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
    """
    logger.info("[INTEREST ACCRUAL] 🔄 Force interest accrual triggered")
    return run_interest_accrual()


@shared_task
def accrue_interest_for_debt(debt_id, as_of_date=None):
    """
    Accrue interest for a specific debt.
    """
    try:
        logger.info(f"[INTEREST ACCRUAL] Accruing interest for debt #{debt_id}")
        debt = Debt.objects.filter(id=debt_id).first()
        if not debt:
            return {
                "debt_id": debt_id,
                "success": False,
                "message": f"Debt #{debt_id} not found",
            }

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

        updated_debt = InterestAccrualService.apply_accrual(debt, as_of_date)
        return {
            "debt_id": debt_id,
            "success": True,
            "message": "Interest accrued successfully",
            "new_balance": float(updated_debt.remaining_amount),
            "last_accrual_date": (
                updated_debt.last_interest_accrual_date.isoformat()
                if updated_debt.last_interest_accrual_date
                else None
            ),
        }
    except Exception as e:
        logger.error(f"[INTEREST ACCRUAL] Error accruing interest for debt #{debt_id}: {e}")
        return {
            "debt_id": debt_id,
            "success": False,
            "message": str(e),
        }