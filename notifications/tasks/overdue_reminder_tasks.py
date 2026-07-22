# notifications/tasks/overdue_reminder_tasks.py
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from notifications.services.notification import NotificationService
from system_settings.utils import email_enabled, get_system_setting, overdue_reminder_days

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "overdue_reminder_last_run"


def _already_ran_today():
    """Check if the reminder task already ran today."""
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
    """Mark today as the last run date."""
    cache.set(
        LAST_RUN_KEY,
        {
            'date': timezone.now().isoformat(),
            'timestamp': timezone.now().isoformat(),
        },
        timeout=86400 * 2
    )


def _generate_overdue_reminder_email_html(
    debtor_name, debt, days_overdue, remaining_balance,
    penalty_note, company_name, branch_address,
    contact_email, contact_phone
):
    """Generate HTML content for overdue reminder email."""
    penalty_html = f"<p><em>{penalty_note}</em></p>" if penalty_note else ""
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
    debtor_name, debt, days_overdue, remaining_balance,
    penalty_note, contact_email, contact_phone
):
    """Generate plain text content for overdue reminder email."""
    penalty_text = f"\n{penalty_note}" if penalty_note else ""
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


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def send_overdue_reminders(self):
    """Celery task to send overdue reminder emails to borrowers."""
    logger.info("[OVERDUE REMINDER] Starting overdue reminder task...")
    try:
        if _already_ran_today():
            logger.info("[OVERDUE REMINDER] Already ran today, skipping")
            return {'status': 'skipped', 'message': 'Already ran today', 'sent': 0, 'failed': 0, 'skipped': 0}

        if not email_enabled():
            logger.info("[OVERDUE REMINDER] Email is disabled, skipping")
            return {'status': 'skipped', 'message': 'Email is disabled in system settings', 'sent': 0, 'failed': 0, 'skipped': 0}

        reminder_days = overdue_reminder_days()
        if not reminder_days:
            logger.warning("[OVERDUE REMINDER] overdue_reminder_days setting is empty")
            return {'status': 'skipped', 'message': 'No reminder days configured', 'sent': 0, 'failed': 0, 'skipped': 0}

        company_name = get_system_setting('company_name', 'Collectly')
        branch_address = get_system_setting('branch_location', 'Manila, Philippines')
        contact_email = get_system_setting('smtp_from_email', 'support@collectly.ph')
        contact_phone = get_system_setting('twilio_phone_number', '+63 (2) 8123-4567')

        today = timezone.now().date()
        overdue_debts = Debt.objects.select_related('borrower').filter(
            due_date__lt=today,
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            borrower__email__isnull=False,
            borrower__deleted_at__isnull=True,
        ).exclude(Q(borrower__email='') | Q(borrower__email__isnull=True))

        logger.info(f"[OVERDUE REMINDER] Found {overdue_debts.count()} overdue debts. Reminder days: {reminder_days}")

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
            if days_overdue not in reminder_days:
                skipped_count += 1
                continue

            remaining_balance = debt.remaining_amount
            penalty_note = "Additional penalties may have been applied. Contact us for the exact amount." if days_overdue > 7 else None

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

                from .send_tasks import send_email_task
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

                if debt.status != Debt.Status.OVERDUE:
                    debt.status = Debt.Status.OVERDUE
                    debt.save(update_fields=['status', 'updated_at'])

                logger.info(f"[OVERDUE REMINDER] ✅ Reminder queued for {borrower.email} (debt #{debt.id}, {days_overdue} days overdue)")

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
                logger.error(f"[OVERDUE REMINDER] ❌ Failed to queue reminder for {borrower.email}: {e}")

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

        _mark_ran_today()

        if failed_count > 0:
            try:
                NotificationService.notify_admins_and_staff(
                    title='⚠️ Overdue Reminder Completed with Failures',
                    message=f'Overdue reminders sent: {sent_count} sent, {failed_count} failed, {skipped_count} skipped. Please check logs.',
                    type='error',
                    metadata={'sent': sent_count, 'failed': failed_count, 'skipped': skipped_count, 'reminder_days': reminder_days},
                    user='system'
                )
            except Exception as e:
                logger.warning(f"[OVERDUE REMINDER] Could not send notifications: {e}")

        logger.info(f"[OVERDUE REMINDER] Completed: {sent_count} sent, {failed_count} failed, {skipped_count} skipped")
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
    """Force immediate overdue reminder run."""
    logger.info("[OVERDUE REMINDER] 🔄 Force overdue reminder run triggered")
    return send_overdue_reminders()


@shared_task
def send_reminder_for_specific_debt(debt_id):
    """Send a reminder for a specific debt (manual trigger)."""
    try:
        debt = Debt.objects.select_related('borrower').filter(
            id=debt_id,
            deleted_at__isnull=True
        ).first()
        if not debt:
            return {'debt_id': debt_id, 'success': False, 'message': 'Debt not found'}
        if not debt.borrower or not debt.borrower.email:
            return {'debt_id': debt_id, 'success': False, 'message': 'Borrower has no email address'}

        due_date = debt.due_date
        today = timezone.now().date()
        days_overdue = (today - due_date).days if due_date < today else 0
        if days_overdue <= 0:
            return {'debt_id': debt_id, 'success': False, 'message': 'Debt is not overdue'}

        company_name = get_system_setting('company_name', 'Collectly')
        branch_address = get_system_setting('branch_location', 'Manila, Philippines')
        contact_email = get_system_setting('smtp_from_email', 'support@collectly.ph')
        contact_phone = get_system_setting('twilio_phone_number', '+63 (2) 8123-4567')
        remaining_balance = debt.remaining_amount

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

        from .send_tasks import send_email_task
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
        return {'debt_id': debt_id, 'success': False, 'message': str(e)}