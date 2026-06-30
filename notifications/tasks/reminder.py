import logging
from datetime import datetime, timedelta
from decimal import Decimal
from django.core import cache
from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from notifications.models.notification_log import NotificationLog
from notifications.services.notification import NotificationService
from system_settings.utils import email_enabled, sms_enabled, get_smtp_config
from system_settings.utils.base import get_system_setting, overdue_reminder_days

logger = logging.getLogger(__name__)


# ============================================================
# EMAIL TASK
# ============================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_email_task(self, to, subject, html, text, log_id, is_retry=False):
    """
    Send email asynchronously with retry logic.

    Args:
        to: Recipient email address
        subject: Email subject
        html: HTML content
        text: Plain text content
        log_id: NotificationLog ID for status tracking
        is_retry: Whether this is a retry attempt

    Returns:
        dict: {
            'success': bool,
            'message_id': str or None
        }

    Raises:
        self.retry: If sending fails, retries with exponential backoff
    """
    try:
        # Check if email is enabled
        if not email_enabled():
            logger.warning(f"[Task] Email is disabled, skipping send to {to}")
            _update_log_status(log_id, NotificationLog.Status.FAILED, "Email disabled in system settings")
            return {"success": False, "error": "Email disabled"}

        logger.info(f"[Task] Sending email to {to} (log_id={log_id}, retry={is_retry})")

        # Get SMTP configuration from system settings
        smtp_config = get_smtp_config()
        if not smtp_config.get('host') or not smtp_config.get('from_email'):
            logger.warning(f"[Task] SMTP config incomplete for {to}")
            _update_log_status(log_id, NotificationLog.Status.FAILED, "SMTP configuration incomplete")
            return {"success": False, "error": "SMTP configuration incomplete"}

        # Build email
        from_email = f"{smtp_config.get('from_name', 'Collectly')} <{smtp_config['from_email']}>"
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text or "",
            from_email=from_email,
            to=[to],
            reply_to=[smtp_config.get('from_email')],
        )

        if html:
            msg.attach_alternative(html, "text/html")

        # Send email
        result = msg.send()

        # Update log as sent
        _update_log_status(log_id, NotificationLog.Status.SENT)

        logger.info(f"[Task] Email sent to {to} (log_id={log_id})")
        return {"success": True, "message_id": result}

    except Exception as e:
        logger.error(f"[Task] Failed to send email to {to}: {e}")

        # Update log as failed
        _update_log_status(log_id, NotificationLog.Status.FAILED, str(e))

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_countdown = 2 ** self.request.retries
            logger.info(f"[Task] Retrying email to {to} in {retry_countdown}s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=retry_countdown)

        logger.error(f"[Task] All retries exhausted for email to {to}")
        return {"success": False, "error": str(e)}


# ============================================================
# SMS TASK
# ============================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_sms_task(self, to, message, log_id=None):
    """
    Send SMS asynchronously with retry logic.

    Args:
        to: Recipient phone number
        message: SMS message content
        log_id: NotificationLog ID for status tracking

    Returns:
        dict: {
            'success': bool,
            'sid': str or None
        }

    Raises:
        self.retry: If sending fails, retries with exponential backoff
    """
    try:
        # Check if SMS is enabled
        if not sms_enabled():
            logger.warning(f"[Task] SMS is disabled, skipping send to {to}")
            if log_id:
                _update_log_status(log_id, NotificationLog.Status.FAILED, "SMS disabled in system settings")
            return {"success": False, "error": "SMS disabled"}

        logger.info(f"[Task] Sending SMS to {to} (log_id={log_id})")

        # Use Twilio SMS service
        from notifications.services.sms import SmsService
        sms_service = SmsService()

        if not sms_service.client:
            error_msg = "SMS service not configured (Twilio credentials missing)"
            logger.error(f"[Task] {error_msg}")
            if log_id:
                _update_log_status(log_id, NotificationLog.Status.FAILED, error_msg)
            return {"success": False, "error": error_msg}

        # Send SMS
        result = sms_service.send(to, message)

        # Update log as sent
        if log_id:
            _update_log_status(log_id, NotificationLog.Status.SENT)

        logger.info(f"[Task] SMS sent to {to} (log_id={log_id})")
        return {"success": True, "sid": result.get("sid")}

    except Exception as e:
        logger.error(f"[Task] Failed to send SMS to {to}: {e}")

        # Update log as failed
        if log_id:
            _update_log_status(log_id, NotificationLog.Status.FAILED, str(e))

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_countdown = 2 ** self.request.retries
            logger.info(f"[Task] Retrying SMS to {to} in {retry_countdown}s (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=retry_countdown)

        logger.error(f"[Task] All retries exhausted for SMS to {to}")
        return {"success": False, "error": str(e)}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _update_log_status(log_id, status, error_message=None):
    """
    Update notification log status.

    Args:
        log_id: ID of the NotificationLog entry
        status: New status (QUEUED, SENT, FAILED, RESEND)
        error_message: Error message if status is FAILED
    """
    if not log_id:
        return

    try:
        log_entry = NotificationLog.objects.get(id=log_id)

        # Only update if status is different
        if log_entry.status != status:
            old_status = log_entry.status
            log_entry.status = status

            if status == NotificationLog.Status.SENT:
                log_entry.sent_at = timezone.now()
                log_entry.error_message = None
            elif status == NotificationLog.Status.FAILED:
                log_entry.last_error_at = timezone.now()
                log_entry.error_message = error_message
            elif status == NotificationLog.Status.QUEUED:
                # Reset error state when requeuing for retry
                log_entry.error_message = None

            log_entry.save()

            logger.debug(f"[Task] NotificationLog #{log_id} status updated: {old_status} → {status}")

    except NotificationLog.DoesNotExist:
        logger.warning(f"[Task] NotificationLog #{log_id} not found")
    except Exception as e:
        logger.error(f"[Task] Failed to update NotificationLog #{log_id}: {e}")


# ============================================================
# SCHEDULED TASKS
# ============================================================

@shared_task
def retry_failed_notifications():
    """Periodic task to retry failed notifications."""
    logger.info("[Task] Retrying failed notifications...")

    failed_logs = NotificationLog.objects.filter(
        status=NotificationLog.Status.FAILED,
        retry_count__lt=3,
    )

    count = 0
    skipped = 0

    for log_entry in failed_logs:
        try:
            if not email_enabled():
                skipped += 1
                continue

            log_entry.retry_count += 1
            log_entry.status = NotificationLog.Status.QUEUED
            log_entry.error_message = None
            log_entry.save()

            send_email_task.delay(
                to=log_entry.recipient_email,
                subject=log_entry.subject or "Notification",
                html=log_entry.payload or "",
                text=log_entry.payload or "",
                log_id=log_entry.id,
                is_retry=True,
            )
            count += 1

        except Exception as e:
            logger.error(f"[Task] Failed to queue retry for log #{log_entry.id}: {e}")

    # Notify admins/staff if there were issues
    if count > 0 or skipped > 0:
        from notifications.services.notification import NotificationService
        NotificationService.notify_admins_and_staff(
            title='📧 Notification Retry Batch',
            message=f'Retry batch completed: {count} queued, {skipped} skipped.',
            type='info',
            metadata={
                'queued': count,
                'skipped': skipped,
            },
            user='system'
        )

    return {'retried': count, 'skipped': skipped}

@shared_task
def cleanup_old_notification_logs(days=90):
    """
    Clean up old notification logs.

    Args:
        days: Number of days to keep (default: 90)

    Returns:
        dict: {
            'deleted': int,
            'message': str
        }
    """
    try:
        logger.info(f"[Task] Cleaning up notification logs older than {days} days...")

        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = NotificationLog.objects.filter(
            created_at__lt=cutoff_date
        ).delete()

        result = {
            'deleted': deleted_count,
            'message': f'Deleted {deleted_count} notification logs older than {days} days'
        }

        logger.info(f"[Task] {result['message']}")
        return result

    except Exception as e:
        logger.error(f"[Task] Failed to cleanup notification logs: {e}")
        return {
            'deleted': 0,
            'message': f'Cleanup failed: {str(e)}'
        }


@shared_task
def send_scheduled_notifications():
    """
    Send scheduled notifications that are due.

    Finds notifications with scheduled_for <= now and status = QUEUED.
    """
    logger.info("[Task] Sending scheduled notifications...")

    now = timezone.now()
    scheduled_logs = NotificationLog.objects.filter(
        status=NotificationLog.Status.QUEUED,
        created_at__lte=now,  # Using created_at as proxy for scheduled_for
    )

    count = 0
    errors = 0

    for log_entry in scheduled_logs:
        try:
            # Determine if email or SMS
            if log_entry.recipient_email:
                send_email_task.delay(
                    to=log_entry.recipient_email,
                    subject=log_entry.subject or "Notification",
                    html=log_entry.payload or "",
                    text=log_entry.payload or "",
                    log_id=log_entry.id,
                )
                count += 1
            else:
                logger.warning(f"[Task] NotificationLog #{log_entry.id} has no recipient")
                errors += 1

        except Exception as e:
            logger.error(f"[Task] Failed to send scheduled notification #{log_entry.id}: {e}")
            errors += 1

    result = {
        'sent': count,
        'errors': errors,
        'message': f'Sent {count} scheduled notifications, {errors} errors'
    }

    logger.info(f"[Task] {result['message']}")
    return result

# Cache key for last run tracking
LAST_RUN_KEY = "overdue_reminder_last_run"


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_overdue_reminders(self):
    """
    Celery task to send overdue reminder emails to borrowers.

    This task runs on a schedule (default: daily at 9 AM) and sends
    reminder emails to borrowers with overdue debts on configured days
    of being overdue.

    Returns:
        dict: {
            'status': str,
            'sent': int,
            'failed': int,
            'skipped': int,
            'message': str,
        }
    """
    logger.info("[OVERDUE REMINDER] Starting overdue reminder task...")

    try:
        # Check if already ran today
        if _already_ran_today():
            logger.info("[OVERDUE REMINDER] Already ran today, skipping")
            return {
                'status': 'skipped',
                'message': 'Already ran today',
                'sent': 0,
                'failed': 0,
                'skipped': 0,
            }

        # Check if email is enabled
        if not email_enabled():
            logger.info("[OVERDUE REMINDER] Email is disabled, skipping")
            return {
                'status': 'skipped',
                'message': 'Email is disabled in system settings',
                'sent': 0,
                'failed': 0,
                'skipped': 0,
            }

        # Get reminder days configuration
        reminder_days = overdue_reminder_days()
        if not reminder_days:
            logger.warning("[OVERDUE REMINDER] overdue_reminder_days setting is empty")
            return {
                'status': 'skipped',
                'message': 'No reminder days configured',
                'sent': 0,
                'failed': 0,
                'skipped': 0,
            }

        # Get system settings for email template
        company_name = get_system_setting('company_name', 'Collectly')
        branch_address = get_system_setting('branch_location', 'Manila, Philippines')
        contact_email = get_system_setting('smtp_from_email', 'support@collectly.ph')
        contact_phone = get_system_setting('twilio_phone_number', '+63 (2) 8123-4567')

        # Find overdue debts with borrowers who have emails
        today = timezone.now().date()
        overdue_debts = Debt.objects.select_related('borrower').filter(
            due_date__lt=today,
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            borrower__email__isnull=False,
            borrower__deleted_at__isnull=True,
        ).exclude(
            Q(borrower__email='') | Q(borrower__email__isnull=True)
        )

        logger.info(
            f"[OVERDUE REMINDER] Found {overdue_debts.count()} overdue debts. "
            f"Reminder days: {reminder_days}"
        )

        sent_count = 0
        failed_count = 0
        skipped_count = 0
        reminder_results = []

        for debt in overdue_debts:
            borrower = debt.borrower
            if not borrower or not borrower.email:
                continue

            due_date = debt.due_date
            days_overdue = (today - due_date).days

            # Check if this debt qualifies for a reminder on this day
            if days_overdue not in reminder_days:
                skipped_count += 1
                continue

            # Prepare email content
            remaining_balance = debt.remaining_amount
            penalty_note = None
            if days_overdue > 7:
                penalty_note = "Additional penalties may have been applied. Contact us for the exact amount."

            # Send email via Celery task
            try:
                subject = f"⏰ Overdue Reminder – {debt.name}"

                html = _generate_overdue_reminder_email_html(
                    debtor_name=borrower.name,
                    debt=debt,
                    days_overdue=days_overdue,
                    remaining_balance=remaining_balance,
                    penalty_note=penalty_note,
                    company_name=company_name,
                    branch_address=branch_address,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                )

                text = _generate_overdue_reminder_email_text(
                    debtor_name=borrower.name,
                    debt=debt,
                    days_overdue=days_overdue,
                    remaining_balance=remaining_balance,
                    penalty_note=penalty_note,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                )

                # Queue email
                send_email_task.delay(
                    to=borrower.email,
                    subject=subject,
                    html=html,
                    text=text,
                    log_id=None,
                    is_retry=False,
                )

                sent_count += 1
                reminder_results.append({
                    'debt_id': debt.id,
                    'borrower_id': borrower.id,
                    'email': borrower.email,
                    'days_overdue': days_overdue,
                    'status': 'sent',
                })

                # Update debt status to overdue if not already
                if debt.status != Debt.Status.OVERDUE:
                    debt.status = Debt.Status.OVERDUE
                    debt.save(update_fields=['status', 'updated_at'])

                logger.info(
                    f"[OVERDUE REMINDER] ✅ Reminder queued for {borrower.email} "
                    f"(debt #{debt.id}, {days_overdue} days overdue)"
                )

            except Exception as e:
                failed_count += 1
                reminder_results.append({
                    'debt_id': debt.id,
                    'borrower_id': borrower.id,
                    'email': borrower.email,
                    'days_overdue': days_overdue,
                    'status': 'failed',
                    'error': str(e),
                })
                logger.error(
                    f"[OVERDUE REMINDER] ❌ Failed to queue reminder for {borrower.email}: {e}"
                )

        # Log the run
        log_audit_event(
            request=None,
            user='system',
            action_type='export_data',
            model_name='OverdueReminder',
            object_id='batch',
            changes={
                'sent': sent_count,
                'failed': failed_count,
                'skipped': skipped_count,
                'reminder_days': reminder_days,
                'date': timezone.now().isoformat(),
            }
        )

        # Mark as ran today
        _mark_ran_today()

        # Send admin notification if there were failures
        if failed_count > 0:
            try:
                from notifications.services.notification import NotificationService
                NotificationService.notify_admins_and_staff(
                    title='⚠️ Overdue Reminder Completed with Failures',
                    message=(
                        f'Overdue reminders sent: {sent_count} sent, '
                        f'{failed_count} failed, {skipped_count} skipped. '
                        f'Please check logs for details.'
                    ),
                    type='error',
                    metadata={
                        'sent': sent_count,
                        'failed': failed_count,
                        'skipped': skipped_count,
                        'reminder_days': reminder_days,
                    },
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[OVERDUE REMINDER] Could not send notifications: {e}")

        logger.info(
            f"[OVERDUE REMINDER] Completed: {sent_count} sent, "
            f"{failed_count} failed, {skipped_count} skipped"
        )

        return {
            'status': 'completed' if failed_count == 0 else 'completed_with_failures',
            'sent': sent_count,
            'failed': failed_count,
            'skipped': skipped_count,
            'message': f'{sent_count} sent, {failed_count} failed, {skipped_count} skipped',
            'results': reminder_results,
        }

    except Exception as e:
        logger.error(f"[OVERDUE REMINDER] ❌ Error during overdue reminder task: {e}")

        # Send failure notification
        try:
            NotificationService.create(
                data={
                    'title': '❌ Overdue Reminder Task Failed',
                    'message': f'Failed to send overdue reminders: {str(e)}',
                    'type': 'error',
                    'metadata': {'error': str(e)},
                },
                user='system',
                request=None
            )
        except Exception as notif_err:
            logger.warning(f"[OVERDUE REMINDER] Could not send failure notification: {notif_err}")

        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


@shared_task
def force_overdue_reminders():
    """
    Force immediate overdue reminder run.
    This is used for manual triggers from admin panel.
    """
    logger.info("[OVERDUE REMINDER] 🔄 Force overdue reminder run triggered")
    return send_overdue_reminders()


@shared_task
def send_reminder_for_specific_debt(debt_id):
    """
    Send a reminder for a specific debt (manual trigger).

    Args:
        debt_id: ID of the debt

    Returns:
        dict: {
            'debt_id': int,
            'success': bool,
            'message': str,
        }
    """
    try:
        debt = Debt.objects.select_related('borrower').filter(
            id=debt_id,
            deleted_at__isnull=True
        ).first()

        if not debt:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Debt not found',
            }

        if not debt.borrower or not debt.borrower.email:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Borrower has no email address',
            }

        due_date = debt.due_date
        today = timezone.now().date()
        days_overdue = (today - due_date).days if due_date < today else 0

        if days_overdue <= 0:
            return {
                'debt_id': debt_id,
                'success': False,
                'message': 'Debt is not overdue',
            }

        # Get system settings
        company_name = get_system_setting('company_name', 'Collectly')
        branch_address = get_system_setting('branch_location', 'Manila, Philippines')
        contact_email = get_system_setting('smtp_from_email', 'support@collectly.ph')
        contact_phone = get_system_setting('twilio_phone_number', '+63 (2) 8123-4567')
        remaining_balance = debt.remaining_amount

        # Send email
        subject = f"⏰ Overdue Reminder – {debt.name}"
        html = _generate_overdue_reminder_email_html(
            debtor_name=debt.borrower.name,
            debt=debt,
            days_overdue=days_overdue,
            remaining_balance=remaining_balance,
            penalty_note=None,
            company_name=company_name,
            branch_address=branch_address,
            contact_email=contact_email,
            contact_phone=contact_phone,
        )
        text = _generate_overdue_reminder_email_text(
            debtor_name=debt.borrower.name,
            debt=debt,
            days_overdue=days_overdue,
            remaining_balance=remaining_balance,
            penalty_note=None,
            contact_email=contact_email,
            contact_phone=contact_phone,
        )

        send_email_task.delay(
            to=debt.borrower.email,
            subject=subject,
            html=html,
            text=text,
            log_id=None,
            is_retry=False,
        )

        return {
            'debt_id': debt_id,
            'success': True,
            'message': f'Reminder queued for {debt.borrower.email}',
            'days_overdue': days_overdue,
        }

    except Exception as e:
        logger.error(f"[OVERDUE REMINDER] Error sending reminder for debt #{debt_id}: {e}")
        return {
            'debt_id': debt_id,
            'success': False,
            'message': str(e),
        }


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _already_ran_today():
    """
    Check if the reminder task already ran today.

    Returns:
        bool: True if already ran today
    """
    last_run = cache.get(LAST_RUN_KEY)
    if not last_run:
        return False

    last_run_date = last_run.get('date')
    if not last_run_date:
        return False

    try:
        last_run_date = datetime.fromisoformat(last_run_date).date()
        today = timezone.now().date()
        return last_run_date == today
    except (ValueError, TypeError):
        return False


def _mark_ran_today():
    """
    Mark today as the last run date.
    """
    cache.set(
        LAST_RUN_KEY,
        {
            'date': timezone.now().isoformat(),
            'timestamp': timezone.now().isoformat(),
        },
        timeout=86400 * 2  # 2 days
    )


def _generate_overdue_reminder_email_html(
    debtor_name,
    debt,
    days_overdue,
    remaining_balance,
    penalty_note,
    company_name,
    branch_address,
    contact_email,
    contact_phone,
):
    """
    Generate HTML content for overdue reminder email.

    Args:
        debtor_name: Name of the debtor
        debt: Debt instance
        days_overdue: Number of days overdue
        remaining_balance: Remaining balance
        penalty_note: Optional penalty note
        company_name: Company name
        branch_address: Branch address
        contact_email: Contact email
        contact_phone: Contact phone

    Returns:
        str: HTML email content
    """
    penalty_html = ""
    if penalty_note:
        penalty_html = f"<p><em>{penalty_note}</em></p>"

    return f"""
    <html>
    <body>
        <h2>⏰ Overdue Reminder</h2>
        <p>Dear {debtor_name},</p>
        <p>This is a reminder that your payment for the following debt is overdue.</p>
        <h3>Debt Details</h3>
        <ul>
            <li><strong>Debt ID:</strong> #{debt.id}</li>
            <li><strong>Debt Name:</strong> {debt.name}</li>
            <li><strong>Original Amount:</strong> ₱{debt.total_amount:,.2f}</li>
            <li><strong>Amount Paid:</strong> ₱{debt.paid_amount:,.2f}</li>
            <li><strong>Remaining Balance:</strong> ₱{remaining_balance:,.2f}</li>
            <li><strong>Due Date:</strong> {debt.due_date.strftime('%B %d, %Y')}</li>
            <li><strong>Days Overdue:</strong> {days_overdue}</li>
        </ul>
        {penalty_html}
        <p>Please settle your outstanding balance immediately to avoid additional penalties and legal action.</p>
        <p>If you have already made a payment, please disregard this message.</p>
        <br>
        <p>
            <strong>{company_name}</strong><br>
            {branch_address}<br>
            Email: {contact_email}<br>
            Phone: {contact_phone}
        </p>
    </body>
    </html>
    """


def _generate_overdue_reminder_email_text(
    debtor_name,
    debt,
    days_overdue,
    remaining_balance,
    penalty_note,
    contact_email,
    contact_phone,
):
    """
    Generate plain text content for overdue reminder email.

    Args:
        debtor_name: Name of the debtor
        debt: Debt instance
        days_overdue: Number of days overdue
        remaining_balance: Remaining balance
        penalty_note: Optional penalty note
        contact_email: Contact email
        contact_phone: Contact phone

    Returns:
        str: Plain text email content
    """
    penalty_text = ""
    if penalty_note:
        penalty_text = f"\n{penalty_note}"

    return f"""
Dear {debtor_name},

This is a reminder that your payment for the following debt is overdue.

Debt Details:
- Debt ID: #{debt.id}
- Debt Name: {debt.name}
- Original Amount: ₱{debt.total_amount:,.2f}
- Amount Paid: ₱{debt.paid_amount:,.2f}
- Remaining Balance: ₱{remaining_balance:,.2f}
- Due Date: {debt.due_date.strftime('%B %d, %Y')}
- Days Overdue: {days_overdue}
{penalty_text}

Please settle your outstanding balance immediately to avoid additional penalties and legal action.

If you have already made a payment, please disregard this message.

Contact us:
Email: {contact_email}
Phone: {contact_phone}
"""