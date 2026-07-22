# loan_applications/tasks/auto_approve_tasks.py
import logging

from celery import shared_task
from django.utils import timezone

from loan_applications.models.loan_application import LoanApplication
from loan_applications.services.loan_application import LoanApplicationService
from loan_applications.state_transitions.loan_application import (
    LoanApplicationStateTransitionService,
)
from borrowers.services.credit_check import CreditCheckService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_approve_applications(self, limit: int = 50, user: str = "system"):
    """
    Automatically approve pending loan applications based on credit score thresholds.
    """
    logger.info("[LOAN APPLICATION TASK] Starting auto-approval for pending applications...")

    try:
        from system_settings.utils import (
            min_credit_score_for_approval,
            max_loan_amount,
            enforce_credit_check,
            credit_check_validity_days,
        )

        pending_apps = (
            LoanApplication.objects.select_related("debtor")
            .filter(
                status=LoanApplication.Status.PENDING,
                deleted_at__isnull=True,
                debtor__isnull=False,
                debtor__deleted_at__isnull=True,
            )
            .order_by("created_at")[:limit]
        )

        total = pending_apps.count()
        logger.info(f"[LOAN APPLICATION TASK] Found {total} pending applications")

        if total == 0:
            return {
                "approved": 0,
                "rejected": 0,
                "errors": [],
                "message": "No pending applications to process",
            }

        approved_count = 0
        rejected_count = 0
        errors = []

        min_score = min_credit_score_for_approval()
        max_amount = max_loan_amount()
        need_credit_check = enforce_credit_check()

        for app in pending_apps:
            try:
                if not app.debtor_id:
                    rejected_count += 1
                    errors.append({
                        "application_id": app.id,
                        "error": "No debtor associated with application",
                    })
                    continue

                if max_amount > 0 and app.requested_amount > max_amount:
                    rejected_count += 1
                    errors.append({
                        "application_id": app.id,
                        "error": f"Amount exceeds max loan amount (₱{max_amount:,.2f})",
                    })
                    continue

                if need_credit_check and min_score > 0:
                    latest_check = CreditCheckService.get_latest(app.debtor_id)

                    if not latest_check:
                        rejected_count += 1
                        errors.append({
                            "application_id": app.id,
                            "error": f"No credit check found for debtor ID {app.debtor_id}",
                        })
                        continue

                    validity_days = credit_check_validity_days()
                    check_date = (
                        latest_check.date_checked.date()
                        if latest_check.date_checked
                        else None
                    )
                    if check_date:
                        days_since_check = (timezone.now().date() - check_date).days
                        if days_since_check > validity_days:
                            rejected_count += 1
                            errors.append({
                                "application_id": app.id,
                                "error": f"Credit check too old ({days_since_check} days)",
                            })
                            continue

                    if latest_check.score < min_score:
                        rejected_count += 1
                        errors.append({
                            "application_id": app.id,
                            "error": f"Credit score {latest_check.score} below minimum {min_score}",
                        })
                        continue

                service = LoanApplicationService()
                approved_app = service.approve(app.id, user=user, request=None)

                transition = LoanApplicationStateTransitionService()
                transition.on_approve(approved_app, user=user, request=None)

                approved_count += 1
                logger.info(f"[LOAN APPLICATION TASK] Auto-approved application #{app.id}")

            except Exception as e:
                rejected_count += 1
                errors.append({"application_id": app.id, "error": str(e)})
                logger.error(f"[LOAN APPLICATION TASK] Failed to process application #{app.id}: {e}")

        if approved_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title="🔄 Auto-Approval Task Completed",
                message=f"Auto-approved: {approved_count} applications, {rejected_count} rejected/failed.",
                type="info" if not errors else "error",
                metadata={
                    "approved": approved_count,
                    "rejected": rejected_count,
                    "total": total,
                    "errors": errors[:10],
                },
                user=user,
            )

        result = {
            "approved": approved_count,
            "rejected": rejected_count,
            "total": total,
            "errors": errors,
            "message": f"Approved {approved_count} applications, {rejected_count} failed",
        }

        logger.info(f"[LOAN APPLICATION TASK] Auto-approval completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Auto-approval failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_auto_approve(user: str = "system"):
    """Force immediate auto-approval run."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force auto-approval triggered")
    return auto_approve_applications(user=user)