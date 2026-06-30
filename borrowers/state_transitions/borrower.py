import logging
import re
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings

from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from borrowers.models.credit_check_log import CreditCheckLog
from debts.models.debt import Debt
from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from loan_agreements.models.loan_agreement import LoanAgreement
from notifications.models.notification import Notification
from notifications.services.notification import NotificationService
from notifications.tasks import send_email_task
from notifications.email_templates.borrower_status import (
    generate_activated_email,
    generate_deactivated_email,
    generate_merged_email,
)
from system_settings.utils import (
    email_enabled,
    sms_enabled,
    get_system_setting,
)

logger = logging.getLogger(__name__)


class BorrowerStateTransitionService:
    """
    Service for handling borrower state transitions.
    
    Handles activation, deactivation, and merging of borrower accounts.
    Manages notifications, email/SMS alerts, and related record updates.
    """

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    def _get_email_data():
        """
        Get common email configuration data from system settings.

        Returns:
            dict: Company name, branch address, contact email, and phone
        """
        return {
            'company_name': get_system_setting('company_name', 'Collectly'),
            'branch_address': get_system_setting('branch_location', 'Manila, Philippines'),
            'contact_email': get_system_setting('smtp_from_email', 'support@collectly.ph'),
            'contact_phone': get_system_setting('twilio_phone_number', '+63 (2) 8123-4567'),
        }

    @staticmethod
    def _send_email(recipient, subject, html, text=None, user="system"):
        """
        Send email using Celery task.

        Args:
            recipient: Email recipient
            subject: Email subject
            html: HTML content
            text: Plain text content (optional, will strip HTML if not provided)
            user: User performing the action
        """
        if text is None and html:
            # Strip HTML tags to get plain text
            text = re.sub(r'<[^>]+>', '', html)

        try:
            send_email_task.delay(
                to=recipient,
                subject=subject,
                html=html,
                text=text or "",
                log_id=None,
                is_retry=False,
            )
            logger.info(f"[Email] Queued email to {recipient}: {subject}")
            return True
        except Exception as e:
            logger.error(f"[Email] Failed to queue email to {recipient}: {e}")
            return False

    @staticmethod
    def _send_sms(phone_number, message, user="system"):
        """
        Send SMS (placeholder - implement with Twilio).

        Args:
            phone_number: Recipient phone number
            message: SMS message
            user: User performing the action
        """
        logger.info(f"[SMS] Would send to {phone_number}: {message}")
        return True

    @staticmethod
    def _send_in_app_notification(title, message, metadata=None, user="system"):
        """
        Send in-app notification.

        Args:
            title: Notification title
            message: Notification message
            metadata: Additional metadata
            user: User performing the action
        """
        try:
            NotificationService.create(
                data={
                    'title': title,
                    'message': message,
                    'type': 'info',
                    'metadata': metadata or {},
                },
                user=user,
                request=None
            )
            return True
        except Exception as e:
            logger.error(f"[Notification] Failed to create notification: {e}")
            return False

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_activate(borrower, user="system", request=None):
        """
        Activate a borrower (restore from soft delete).

        Args:
            borrower: Borrower instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Borrower: The activated borrower instance

        Raises:
            ValidationError: If borrower is already active
        """
        logger.info(f"[BorrowerTransition] on_activate: borrower_id={borrower.id}, user={user}")

        # Check if already active
        if not borrower.deleted_at:
            raise ValidationError({'detail': 'Borrower is already active.'})

        # Capture old state for audit
        old_deleted_at = borrower.deleted_at

        # Restore borrower
        borrower.deleted_at = None
        borrower.updated_at = timezone.now()
        borrower.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='borrower_restore',
            model_name='Borrower',
            object_id=str(borrower.id),
            changes={
                'before': {'deleted_at': old_deleted_at},
                'after': {'deleted_at': None},
            }
        )

        # In-app notification
        BorrowerStateTransitionService._send_in_app_notification(
            title="Account Reactivated",
            message=f'Borrower "{borrower.name}" has been reactivated.',
            metadata={'borrower_id': borrower.id},
            user=user,
        )

        # Send email notification if enabled
        if email_enabled() and borrower.email:
            email_data = BorrowerStateTransitionService._get_email_data()
            html = generate_activated_email({
                'borrower_id': borrower.id,
                'borrower_name': borrower.name,
                'borrower_email': borrower.email,
                'borrower_contact': borrower.contact,
                **email_data,
            })
            BorrowerStateTransitionService._send_email(
                recipient=borrower.email,
                subject="✅ Account Reactivated",
                html=html,
                user=user,
            )

        # Send SMS notification if enabled
        if sms_enabled() and borrower.contact:
            message = f"Dear {borrower.name}, your account has been reactivated."
            BorrowerStateTransitionService._send_sms(
                phone_number=borrower.contact,
                message=message,
                user=user,
            )

        logger.info(f"[BorrowerTransition] Borrower #{borrower.id} activated")
        return borrower

    @staticmethod
    @transaction.atomic
    def on_deactivate(borrower, user="system", request=None):
        """
        Deactivate (soft delete) a borrower.

        Also marks all active debts as defaulted.

        Args:
            borrower: Borrower instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Borrower: The deactivated borrower instance

        Raises:
            ValidationError: If borrower is already deactivated
        """
        logger.info(f"[BorrowerTransition] on_deactivate: borrower_id={borrower.id}, user={user}")

        # Check if already deactivated
        if borrower.deleted_at:
            raise ValidationError({'detail': 'Borrower is already deactivated.'})

        # Soft delete borrower
        borrower.deleted_at = timezone.now()
        borrower.updated_at = timezone.now()
        borrower.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='borrower_delete',
            model_name='Borrower',
            object_id=str(borrower.id),
            changes={'deleted_at': borrower.deleted_at}
        )

        # Mark all active debts as defaulted
        active_debts = Debt.objects.filter(
            borrower=borrower,
            status=Debt.Status.ACTIVE,
            deleted_at__isnull=True
        )

        debt_count = 0
        for debt in active_debts:
            debt.status = Debt.Status.DEFAULTED
            debt.updated_at = timezone.now()
            debt.save()
            debt_count += 1

        logger.warning(
            f"[BorrowerTransition] Borrower #{borrower.id} deactivated. "
            f"{debt_count} active debts set to defaulted."
        )

        # In-app notification
        BorrowerStateTransitionService._send_in_app_notification(
            title="Borrower Deactivated",
            message=f'Borrower "{borrower.name}" has been deactivated. '
                    f'{debt_count} active debts marked as defaulted.',
            metadata={'borrower_id': borrower.id, 'debt_count': debt_count},
            user=user,
        )

        # Send email notification if enabled
        if email_enabled() and borrower.email:
            email_data = BorrowerStateTransitionService._get_email_data()
            html = generate_deactivated_email({
                'borrower_id': borrower.id,
                'borrower_name': borrower.name,
                'borrower_email': borrower.email,
                'borrower_contact': borrower.contact,
                'active_debt_count': debt_count,
                **email_data,
            })
            BorrowerStateTransitionService._send_email(
                recipient=borrower.email,
                subject="⚠️ Account Deactivated",
                html=html,
                user=user,
            )

        logger.info(f"[BorrowerTransition] Borrower #{borrower.id} deactivated")
        return borrower

    @staticmethod
    @transaction.atomic
    def on_merge(source_borrower, target_borrower, user="system", request=None):
        """
        Merge source borrower into target borrower.

        Transfers all related records (debts, payments, penalties, agreements, notifications)
        from source to target. Source borrower is soft-deleted.

        Args:
            source_borrower: Borrower instance to merge from
            target_borrower: Borrower instance to merge into
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            dict: {
                'source': source_borrower,
                'target': target_borrower,
                'debts_transferred': int
            }

        Raises:
            ValidationError: If source and target are the same
        """
        logger.info(
            f"[BorrowerTransition] on_merge: source_id={source_borrower.id} "
            f"into target_id={target_borrower.id}, user={user}"
        )

        # Cannot merge into itself
        if source_borrower.id == target_borrower.id:
            raise ValidationError({'detail': 'Cannot merge a borrower into itself.'})

        # Transfer debts
        debts_to_transfer = Debt.objects.filter(
            borrower=source_borrower,
            deleted_at__isnull=True
        )
        debts_transferred = debts_to_transfer.count()

        for debt in debts_to_transfer:
            debt.borrower = target_borrower
            debt.updated_at = timezone.now()
            debt.save()

        # Transfer payments (already linked via debt, no direct borrower field)
        payments = PaymentTransaction.objects.filter(
            debt__borrower=source_borrower,
            deleted_at__isnull=True
        )
        for payment in payments:
            payment.updated_at = timezone.now()
            payment.save()

        # Transfer penalties
        penalties = PenaltyTransaction.objects.filter(
            debt__borrower=source_borrower,
            deleted_at__isnull=True
        )
        for penalty in penalties:
            penalty.updated_at = timezone.now()
            penalty.save()

        # Transfer loan agreements
        agreements = LoanAgreement.objects.filter(
            debt__borrower=source_borrower,
            deleted_at__isnull=True
        )
        for agreement in agreements:
            agreement.updated_at = timezone.now()
            agreement.save()

        # Transfer notifications
        notifications = Notification.objects.filter(
            debt__borrower=source_borrower,
            deleted_at__isnull=True
        )
        for notification in notifications:
            notification.updated_at = timezone.now()
            notification.save()

        # Audit logs
        log_audit_event(
            request=request,
            user=user,
            action_type='borrower_merge',
            model_name='Borrower',
            object_id=str(source_borrower.id),
            changes={
                'action': 'merged_into',
                'target_borrower_id': target_borrower.id,
                'debts_transferred': debts_transferred,
            }
        )

        log_audit_event(
            request=request,
            user=user,
            action_type='borrower_merge',
            model_name='Borrower',
            object_id=str(target_borrower.id),
            changes={
                'action': 'received_merge',
                'source_borrower_id': source_borrower.id,
                'debts_received': debts_transferred,
            }
        )

        # Soft delete source borrower with merge note
        merge_note = (
            f"[Merged into borrower #{target_borrower.id} "
            f"on {timezone.now().isoformat()}]"
        )
        if source_borrower.notes:
            source_borrower.notes = f"{source_borrower.notes}\n{merge_note}"
        else:
            source_borrower.notes = merge_note

        source_borrower.deleted_at = timezone.now()
        source_borrower.updated_at = timezone.now()
        source_borrower.save()

        # In-app notification
        BorrowerStateTransitionService._send_in_app_notification(
            title="Account Merged",
            message=f'Borrower #{source_borrower.id} has been merged into '
                    f'#{target_borrower.id}. {debts_transferred} debts transferred.',
            metadata={
                'source_id': source_borrower.id,
                'target_id': target_borrower.id,
                'debts_transferred': debts_transferred,
            },
            user=user,
        )

        # Send email notifications if enabled
        if email_enabled():
            email_data = BorrowerStateTransitionService._get_email_data()

            # Generate merged emails (returns dict with 'source' and 'target')
            merged_emails = generate_merged_email({
                'source_borrower_id': source_borrower.id,
                'source_borrower_name': source_borrower.name,
                'target_borrower_id': target_borrower.id,
                'target_borrower_name': target_borrower.name,
                'debts_transferred': debts_transferred,
                **email_data,
            })

            # Email to source borrower
            if source_borrower.email:
                BorrowerStateTransitionService._send_email(
                    recipient=source_borrower.email,
                    subject="📋 Account Merge – Your Account Merged",
                    html=merged_emails['source'],
                    user=user,
                )

            # Email to target borrower
            if target_borrower.email:
                BorrowerStateTransitionService._send_email(
                    recipient=target_borrower.email,
                    subject="📋 Account Merge – New Debts Added",
                    html=merged_emails['target'],
                    user=user,
                )

        logger.info(
            f"[BorrowerTransition] Merge completed: source #{source_borrower.id} "
            f"into target #{target_borrower.id}, {debts_transferred} debts transferred"
        )

        return {
            'source': source_borrower,
            'target': target_borrower,
            'debts_transferred': debts_transferred,
        }