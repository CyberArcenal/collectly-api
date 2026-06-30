import logging
import re
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from loan_agreements.models.loan_agreement import LoanAgreement
from debts.models.debt import Debt
from notifications.services.notification import NotificationService
from notifications.tasks import send_email_task
from notifications.email_templates.loan_agreement import (
    generate_draft_created_email,
    generate_signed_email,
)
from system_settings.utils import (
    email_enabled,
    get_system_setting,
    default_interest_calculation_period,
)

logger = logging.getLogger(__name__)


class LoanAgreementStateTransitionService:
    """
    Service for handling loan agreement state transitions.

    Handles agreement creation (draft), signing, updates, and deletion.
    Manages notifications, email alerts, and audit logging.
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

    @staticmethod
    def _get_debt_with_borrower(debt_id):
        """
        Get debt with borrower relation.

        Args:
            debt_id: Debt ID

        Returns:
            Debt: Debt instance with borrower loaded

        Raises:
            ValidationError: If debt not found
        """
        debt = Debt.objects.select_related('borrower').filter(id=debt_id).first()
        if not debt:
            raise ValidationError({'detail': f'Debt #{debt_id} not found'})
        return debt

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_created(agreement, user="system", request=None):
        """
        Handle post-agreement creation events (draft).

        Args:
            agreement: LoanAgreement instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[LoanAgreementTransition] on_created: agreement_id={agreement.id}, debt_id={agreement.debt_id}, user={user}")

        # Get debt with borrower
        debt = LoanAgreementStateTransitionService._get_debt_with_borrower(agreement.debt_id)
        borrower = debt.borrower

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='loan_agreement_create',
            model_name='LoanAgreement',
            object_id=str(agreement.id),
            changes={
                'debt_id': agreement.debt_id,
                'status': agreement.status,
                'lender_name': agreement.lender_name,
            }
        )

        # In-app notification
        if borrower:
            LoanAgreementStateTransitionService._send_in_app_notification(
                title="📄 Draft Loan Agreement Created",
                message=f'A draft loan agreement for debt "{debt.name}" has been created. It is not yet signed.',
                metadata={
                    'agreement_id': agreement.id,
                    'debt_id': debt.id,
                    'borrower_id': borrower.id,
                },
                user=user,
            )

            # Email notification (draft) if enabled
            if email_enabled() and borrower.email:
                email_data = LoanAgreementStateTransitionService._get_email_data()
                interest_period = debt.interest_calculation_period or default_interest_calculation_period()

                html = generate_draft_created_email({
                    'borrower_name': borrower.name,
                    'agreement_id': agreement.id,
                    'debt_id': debt.id,
                    'debt_name': debt.name,
                    'principal_amount': debt.total_amount,
                    'interest_rate': debt.interest_rate or 0,
                    'interest_period': interest_period,
                    **email_data,
                })

                LoanAgreementStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="📄 Draft Loan Agreement Created",
                    html=html,
                    user=user,
                )

        logger.info(f"[LoanAgreementTransition] Draft agreement #{agreement.id} created")

    @staticmethod
    @transaction.atomic
    def on_signed(agreement, user="system", request=None):
        """
        Handle post-agreement signing events.

        Args:
            agreement: LoanAgreement instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[LoanAgreementTransition] on_signed: agreement_id={agreement.id}, user={user}")

        # Get full agreement with debt and borrower
        full_agreement = LoanAgreement.objects.select_related(
            'debt__borrower'
        ).filter(id=agreement.id).first()

        if not full_agreement:
            raise ValidationError({'detail': f'Agreement #{agreement.id} not found'})

        debt = full_agreement.debt
        if not debt:
            raise ValidationError({'detail': f'Debt not found for agreement #{agreement.id}'})

        borrower = debt.borrower

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='loan_agreement_signed',
            model_name='LoanAgreement',
            object_id=str(agreement.id),
            changes={
                'before': {'status': 'draft'},
                'after': {'status': 'signed', 'signed_by': full_agreement.signed_by or user},
            }
        )

        # In-app notification
        if borrower:
            LoanAgreementStateTransitionService._send_in_app_notification(
                title="✅ Loan Agreement Signed",
                message=f'The loan agreement for debt "{debt.name}" has been officially signed.',
                metadata={
                    'agreement_id': full_agreement.id,
                    'debt_id': debt.id,
                    'borrower_id': borrower.id,
                    'signed_by': full_agreement.signed_by or user,
                },
                user=user,
            )

            # Email notification if enabled
            if email_enabled() and borrower.email:
                email_data = LoanAgreementStateTransitionService._get_email_data()
                interest_period = debt.interest_calculation_period or default_interest_calculation_period()

                html = generate_signed_email({
                    'borrower_name': borrower.name,
                    'agreement_id': full_agreement.id,
                    'debt_id': debt.id,
                    'debt_name': debt.name,
                    'principal_amount': debt.total_amount,
                    'interest_rate': debt.interest_rate or 0,
                    'interest_period': interest_period,
                    'signed_at': full_agreement.signed_at or timezone.now(),
                    'signed_by': full_agreement.signed_by or user,
                    **email_data,
                })

                LoanAgreementStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="✅ Loan Agreement Signed",
                    html=html,
                    user=user,
                )

        logger.info(f"[LoanAgreementTransition] Agreement #{agreement.id} signed")

    @staticmethod
    @transaction.atomic
    def on_updated(old_agreement, new_agreement, user="system", request=None):
        """
        Handle post-agreement update events.

        Args:
            old_agreement: Old LoanAgreement instance
            new_agreement: Updated LoanAgreement instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[LoanAgreementTransition] on_updated: agreement_id={new_agreement.id}, user={user}")

        # Track changes for audit
        changes = {}
        if old_agreement.status != new_agreement.status:
            changes['status'] = {'old': old_agreement.status, 'new': new_agreement.status}
        if old_agreement.lender_name != new_agreement.lender_name:
            changes['lender_name'] = {'old': old_agreement.lender_name, 'new': new_agreement.lender_name}

        if changes:
            log_audit_event(
                request=request,
                user=user,
                action_type='loan_agreement_update',
                model_name='LoanAgreement',
                object_id=str(new_agreement.id),
                changes=changes
            )

        logger.info(f"[LoanAgreementTransition] Agreement #{new_agreement.id} updated")

    @staticmethod
    @transaction.atomic
    def on_before_delete(agreement, user="system", request=None):
        """
        Handle pre-agreement deletion validation.

        Args:
            agreement: LoanAgreement instance
            user: User performing the action
            request: HTTP request object for audit

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[LoanAgreementTransition] on_before_delete: agreement_id={agreement.id}, user={user}")

        # Check if agreement is signed and if we should prevent deletion
        if agreement.status == LoanAgreement.Status.SIGNED:
            logger.warning(
                f"[LoanAgreementTransition] Attempting to delete signed agreement #{agreement.id}"
            )
            # Allow deletion but log warning (can be configured to prevent)

    @staticmethod
    @transaction.atomic
    def on_after_delete(agreement, user="system", request=None):
        """
        Handle post-agreement deletion events.

        Args:
            agreement: LoanAgreement instance (before deletion)
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(f"[LoanAgreementTransition] on_after_delete: agreement_id={agreement.id}, user={user}")

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='loan_agreement_delete',
            model_name='LoanAgreement',
            object_id=str(agreement.id),
            changes={
                'debt_id': agreement.debt_id,
                'status': agreement.status,
                'lender_name': agreement.lender_name,
            }
        )

        logger.info(f"[LoanAgreementTransition] Agreement #{agreement.id} deleted")