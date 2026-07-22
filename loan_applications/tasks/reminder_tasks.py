# loan_applications/tasks/reminder_tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from loan_applications.models.loan_application import LoanApplication
from notifications.models.notification_log import NotificationLog
from notifications.services.notification import NotificationService
from notifications.services.notification_log import NotificationLogService
from notifications.email_templates.loan_status import generate_pending_reminder_email

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_pending_application_reminders(self, days: int = 7, user: str = "system"):
    """
    Send reminders for pending applications that have been waiting for more than N days.
    """
    logger.info(f"[LOAN APPLICATION TASK] Sending reminders for pending applications older than {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        pending_old = LoanApplication.objects.select_related("debtor").filter(
            status=LoanApplication.Status.PENDING,
            deleted_at__isnull=True,
            created_at__lt=cutoff,
            debtor__email__isnull=False,
            debtor__deleted_at__isnull=True,
        )

        sent_count = 0
        errors = []

        for app in pending_old:
            try:
                if app.debtor and app.debtor.email:
                    email_data = {
                        "applicant_name": app.debtor_name,
                        "application_id": app.id,
                        "purpose": app.purpose,
                        "amount": app.requested_amount,
                        "days_waiting": (
                            timezone.now().date() - app.created_at.date()
                        ).days,
                    }

                    html = generate_pending_reminder_email(email_data)
                    NotificationLogService.create(
                        data={
                            "channel": NotificationLog.Channel.EMAIL,
                            "recipient": app.debtor.email,
                            "subject": f"📋 Loan Application Update - Pending Review",
                            "payload": html,
                        },
                        user=user,
                        request=None,
                    )
                    sent_count += 1
                    logger.info(
                        f"[LOAN APPLICATION TASK] Sent reminder for application #{app.id} to {app.debtor.email}"
                    )

            except Exception as e:
                errors.append({"application_id": app.id, "error": str(e)})
                logger.error(f"[LOAN APPLICATION TASK] Failed to send reminder for #{app.id}: {e}")

        if sent_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title="📧 Pending Application Reminders Sent",
                message=f"Sent {sent_count} reminders for pending applications.",
                type="info",
                metadata={"sent_count": sent_count, "errors": errors[:10]},
                user=user,
            )

        return {
            "sent_count": sent_count,
            "errors": errors,
            "message": f"Sent {sent_count} reminders",
        }

    except Exception as e:
        logger.error(f"[LOAN APPLICATION TASK] Reminder sending failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task
def force_pending_reminders(user: str = "system", days: int = 7):
    """Force immediate pending reminders."""
    logger.info("[LOAN APPLICATION TASK] 🔄 Force pending reminders triggered")
    return send_pending_application_reminders(days=days, user=user)