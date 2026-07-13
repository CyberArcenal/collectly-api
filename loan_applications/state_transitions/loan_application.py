import logging
import re
import os
from datetime import datetime, timedelta
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings

from audit.utils.log import log_audit_event
from loan_applications.models.loan_application import LoanApplication
from debts.models.debt import Debt
from debts.services.debt import DebtService
from loan_agreements.services.loan_agreement import LoanAgreementService
from notifications.services.notification import NotificationService
from notifications.tasks import send_email_task
from notifications.email_templates.loan_status import (
    generate_submitted_email,
    generate_approved_email,
    generate_rejected_email,
)
from system_settings.utils import (
    enforce_credit_check,
    email_enabled,
    sms_enabled,
    require_loan_agreement,
    loan_agreement_template,
    default_penalty_rate,
    default_loan_term_months,
    default_interest_calculation_period,
    get_system_setting,
)
from borrowers.services.credit_check import CreditCheckService

logger = logging.getLogger(__name__)


class LoanApplicationStateTransitionService:
    """
    Service for handling loan application state transitions.

    Handles application submission, approval, rejection, and reopening.
    On approval, creates the active debt and generates loan agreement PDF.
    Manages notifications, email/SMS alerts, and audit logging.
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
            "company_name": get_system_setting("company_name", "Collectly"),
            "branch_address": get_system_setting(
                "branch_location", "Manila, Philippines"
            ),
            "contact_email": get_system_setting(
                "smtp_from_email", "support@collectly.ph"
            ),
            "contact_phone": get_system_setting(
                "twilio_phone_number", "+63 (2) 8123-4567"
            ),
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
            text = re.sub(r"<[^>]+>", "", html)

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
                    "title": title,
                    "message": message,
                    "type": "info",
                    "metadata": metadata or {},
                },
                user=user,
                request=None,
            )
            return True
        except Exception as e:
            logger.error(f"[Notification] Failed to create notification: {e}")
            return False

    @staticmethod
    def _get_application_with_debtor(application_id):
        """
        Get application with debtor relation.

        Args:
            application_id: Application ID

        Returns:
            LoanApplication: Application instance with debtor loaded

        Raises:
            ValidationError: If application not found
        """
        application = (
            LoanApplication.objects.select_related("debtor")
            .filter(id=application_id, deleted_at__isnull=True)
            .first()
        )
        logger.debug(f"Appication data: {application.__dict__ if application else 'None'}")
        if not application:
            raise ValidationError(
                {"detail": f"Application #{application_id} not found"}
            )

        return application

    @staticmethod
    def _get_company_name():
        """Get company name from system settings."""
        return get_system_setting("company_name", "Collectly")

    @staticmethod
    def _get_contact_email():
        """Get contact email from system settings."""
        return get_system_setting("smtp_from_email", "support@collectly.ph")

    @staticmethod
    def _get_contact_phone():
        """Get contact phone from system settings."""
        return get_system_setting("twilio_phone_number", "+63 (2) 8123-4567")

    @staticmethod
    def _get_branch_address():
        """Get branch address from system settings."""
        return get_system_setting("branch_location", "Manila, Philippines")

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_submit(application, user="system", request=None):
        """
        Handle post-application submission events.

        Args:
            application: LoanApplication instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(
            f"[LoanApplicationTransition] on_submit: "
            f"application_id={application.id}, debtor={application.debtor_name}, "
            f"amount={application.requested_amount}, user={user}"
        )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="loan_application_submit",
            model_name="LoanApplication",
            object_id=str(application.id),
            changes={
                "debtor_name": application.debtor_name,
                "requested_amount": float(application.requested_amount),
                "purpose": application.purpose,
                "proposed_due_date": (
                    application.proposed_due_date.isoformat()
                    if application.proposed_due_date
                    else None
                ),
            },
        )

        # In-app notification to loan officer
        LoanApplicationStateTransitionService._send_in_app_notification(
            title="📋 New Loan Application Submitted",
            message=f'Application #{application.id} from "{application.debtor_name}" '
            f"for ₱{application.requested_amount:,.2f} has been submitted.",
            metadata={
                "application_id": application.id,
                "debtor_name": application.debtor_name,
                "amount": float(application.requested_amount),
            },
            user=user,
        )

        # Send confirmation email to applicant
        if email_enabled() and application.debtor_email:
            email_data = LoanApplicationStateTransitionService._get_email_data()
            html = generate_submitted_email(
                {
                    "applicant_name": application.debtor_name,
                    "application_id": application.id,
                    "purpose": application.purpose,
                    "amount": application.requested_amount,
                    **email_data,
                }
            )
            LoanApplicationStateTransitionService._send_email(
                recipient=application.debtor_email,
                subject="📋 Loan Application Received - Thank You",
                html=html,
                user=user,
            )

        # Trigger credit check if enabled and debtor exists
        if enforce_credit_check() and application.debtor_id:
            try:
                CreditCheckService.perform_credit_check(
                    data={"debtor_id": application.debtor_id},
                    user=user,
                    request=request,
                )
                logger.info(
                    f"[LoanApplicationTransition] Credit check triggered "
                    f"for debtor #{application.debtor_id}"
                )
            except Exception as e:
                logger.error(
                    f"[LoanApplicationTransition] Failed to trigger credit check: {e}"
                )

        logger.info(
            f"[LoanApplicationTransition] Application #{application.id} submitted"
        )

    @staticmethod
    @transaction.atomic
    def on_approve(application, user="system", request=None):
        """
        Handle post-application approval events.

        This is where the active debt is created and loan agreement is generated.

        Args:
            application: LoanApplication instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            dict: {
                'application': LoanApplication,
                'debt': Debt,
                'agreement': LoanAgreement or None
            }
        """
        logger.info(
            f"[LoanApplicationTransition] on_approve: "
            f"application_id={application.id}, user={user}"
        )

        # Reload application with debtor
        app: LoanApplication = (
            LoanApplicationStateTransitionService._get_application_with_debtor(
                application.id
            )
        )

        # 1. Create active debt
        # Determine due date
        due_date = app.proposed_due_date
        if not due_date:
            term_months = default_loan_term_months()
            due_date = timezone.now().date() + timedelta(days=term_months * 30)

        # Get penalty rate
        penalty_rate = default_penalty_rate()

        # Prepare debt data
        debt_data = {
            "borrower_id": app.debtor_id,
            "name": f"Loan: {app.purpose}",
            "total_amount": app.requested_amount,
            "paid_amount": Decimal("0"),
            "due_date": due_date,
            "status": Debt.Status.ACTIVE,
            "interest_rate": app.interest_rate,
            "penalty_rate": penalty_rate,
        }

        # Create debt using DebtService
        debt = DebtService.create(data=debt_data, user=user, request=request)

        logger.info(
            f"[LoanApplicationTransition] Created debt #{debt.id} "
            f"for application #{app.id}"
        )

        # 3. Send notifications
        # In-app notification to debtor
        LoanApplicationStateTransitionService._send_in_app_notification(
            title="🎉 Loan Approved!",
            message=f"Your loan application #{app.id} for ₱{app.requested_amount:,.2f} has been approved.",
            metadata={
                "application_id": app.id,
                "debt_id": debt.id,
                "amount": float(app.requested_amount),
            },
            user=user,
        )

        # Email notification
        if email_enabled() and app.debtor_email:
            email_data = LoanApplicationStateTransitionService._get_email_data()
            term_months = default_loan_term_months()
            interest_period = default_interest_calculation_period()

            html = generate_approved_email(
                {
                    "applicant_name": app.debtor_name,
                    "application_id": app.id,
                    "debt_id": debt.id,
                    "purpose": app.purpose,
                    "amount": app.requested_amount,
                    "interest_rate": app.interest_rate,
                    "interest_period": interest_period,
                    "due_date": debt.due_date,
                    "term_months": term_months,
                    **email_data,
                }
            )
            LoanApplicationStateTransitionService._send_email(
                recipient=app.debtor_email,
                subject="🎉 Loan Approved - Congratulations!",
                html=html,
                user=user,
            )

        # SMS notification
        if sms_enabled() and app.debtor_contact:
            message = (
                f"Congratulations {app.debtor_name}! Your loan of "
                f"₱{app.requested_amount:,.2f} has been approved. "
                f"Due date: {due_date.strftime('%B %d, %Y')}."
            )
            LoanApplicationStateTransitionService._send_sms(
                phone_number=app.debtor_contact,
                message=message,
                user=user,
            )

        # 4. Generate loan agreement if required
        agreement = None
        if require_loan_agreement():
            try:
                agreement = (
                    LoanApplicationStateTransitionService._generate_loan_agreement(
                        application=app, debt=debt, user=user, request=request
                    )
                )
                logger.info(
                    f"[LoanApplicationTransition] Loan agreement generated "
                    f"for debt #{debt.id}"
                )
            except Exception as e:
                logger.error(
                    f"[LoanApplicationTransition] Failed to generate loan agreement: {e}"
                )
                # Don't fail the entire approval because of PDF generation

        # 5. Audit log for debt creation
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_create_from_application",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "application_id": app.id,
                "amount": float(app.requested_amount),
                "interest_rate": (
                    float(app.interest_rate) if app.interest_rate else None
                ),
            },
        )

        logger.info(
            f"[LoanApplicationTransition] Application #{app.id} approved, "
            f"debt #{debt.id} created"
        )

        return {
            "application": app,
            "debt": debt,
            "agreement": agreement,
        }

    @staticmethod
    @transaction.atomic
    def on_reject(application, reason=None, user="system", request=None):
        """
        Handle post-application rejection events.

        Args:
            application: LoanApplication instance
            reason: Rejection reason
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(
            f"[LoanApplicationTransition] on_reject: "
            f"application_id={application.id}, user={user}, reason={reason}"
        )

        # Reload application
        app = LoanApplicationStateTransitionService._get_application_with_debtor(
            application.id
        )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="loan_application_reject",
            model_name="LoanApplication",
            object_id=str(app.id),
            changes={
                "reason": reason,
                "status": LoanApplication.Status.REJECTED,
            },
        )

        # In-app notification to debtor
        LoanApplicationStateTransitionService._send_in_app_notification(
            title="📋 Loan Application Update",
            message=f"Your loan application #{app.id} has been reviewed. "
            f"Please check your email for details.",
            metadata={
                "application_id": app.id,
                "status": LoanApplication.Status.REJECTED,
            },
            user=user,
        )

        # Email notification
        if email_enabled() and app.debtor_email:
            email_data = LoanApplicationStateTransitionService._get_email_data()
            html = generate_rejected_email(
                {
                    "applicant_name": app.debtor_name,
                    "application_id": app.id,
                    "amount": app.requested_amount,
                    "purpose": app.purpose,
                    "rejection_reason": reason
                    or "Application did not meet our lending criteria.",
                    **email_data,
                }
            )
            LoanApplicationStateTransitionService._send_email(
                recipient=app.debtor_email,
                subject="📋 Loan Application Update",
                html=html,
                user=user,
            )

        # SMS notification
        if sms_enabled() and app.debtor_contact:
            message = (
                f"Dear {app.debtor_name}, your loan application has been reviewed. "
                f"Please check your email for the decision details."
            )
            LoanApplicationStateTransitionService._send_sms(
                phone_number=app.debtor_contact,
                message=message,
                user=user,
            )

        logger.info(f"[LoanApplicationTransition] Application #{app.id} rejected")

    @staticmethod
    @transaction.atomic
    def on_reopen(application, user="system", request=None):
        """
        Handle post-application reopening events (rejected → pending).

        Args:
            application: LoanApplication instance
            user: User performing the action
            request: HTTP request object for audit
        """
        logger.info(
            f"[LoanApplicationTransition] on_reopen: "
            f"application_id={application.id}, user={user}"
        )

        # Reload application
        app = LoanApplicationStateTransitionService._get_application_with_debtor(
            application.id
        )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="loan_application_reopen",
            model_name="LoanApplication",
            object_id=str(app.id),
            changes={
                "before": {"status": LoanApplication.Status.REJECTED},
                "after": {"status": LoanApplication.Status.PENDING},
            },
        )

        # In-app notification to loan officer
        LoanApplicationStateTransitionService._send_in_app_notification(
            title="🔄 Loan Application Reopened",
            message=f'Application #{app.id} from "{app.debtor_name}" has been reopened for review.',
            metadata={
                "application_id": app.id,
                "debtor_name": app.debtor_name,
            },
            user=user,
        )

        logger.info(f"[LoanApplicationTransition] Application #{app.id} reopened")

    # ============================================================
    # LOAN AGREEMENT GENERATION
    # ============================================================

    @staticmethod
    def _generate_loan_agreement(application, debt, user="system", request=None):
        """
        Generate loan agreement PDF and create loan agreement record.

        Args:
            application: LoanApplication instance
            debt: Debt instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            LoanAgreement: The created loan agreement instance or None
        """
        try:
            # Get template from settings
            template_name = loan_agreement_template() or "default_loan_agreement.html"

            # Prepare agreement data
            agreement_data = {
                "agreement_id": f"LA-{debt.id}",
                "agreement_date": timezone.now().date().strftime("%B %d, %Y"),
                "lender_name": LoanApplicationStateTransitionService._get_company_name(),
                "borrower_name": application.debtor_name,
                "borrower_email": application.debtor_email or "",
                "borrower_contact": application.debtor_contact or "",
                "borrower_address": application.debtor_address or "",
                "currency": "₱",
                "principal_amount": f"{application.requested_amount:,.2f}",
                "interest_rate": application.interest_rate or 0,
                "penalty_rate": default_penalty_rate(),
                "due_date": (
                    debt.due_date.strftime("%B %d, %Y") if debt.due_date else ""
                ),
                "purpose": application.purpose,
                "loan_start_date": debt.created_at.strftime("%B %d, %Y"),
                "anniversary_day": debt.created_at.day,
                "signature_date": timezone.now().date().strftime("%B %d, %Y"),
            }

            # Generate PDF
            # TODO: Implement PDF generation using ReportLab, WeasyPrint, or django-wkhtmltopdf
            # For now, create a placeholder
            pdf_path = None
            pdf_content = None

            # Create loan agreement record
            from loan_agreements.models.loan_agreement import LoanAgreement

            agreement = LoanAgreement.objects.create(
                debt=debt,
                status=LoanAgreement.Status.DRAFT,
                agreement_date=timezone.now().date(),
                lender_name=agreement_data["lender_name"],
                terms_text="Standard loan agreement with monthly interest accrual.",
                # file=pdf_content,  # Uncomment when PDF generation is implemented
                principal_amount=application.requested_amount,
                interest_rate=application.interest_rate,
                penalty_rate=default_penalty_rate(),
                due_date=debt.due_date,
                purpose=application.purpose,
                loan_start_date=debt.created_at,
                anniversary_day=debt.created_at.day,
            )

            logger.info(
                f"[LoanApplicationTransition] Loan agreement #{agreement.id} created"
            )

            # Optionally sign immediately
            # agreement.status = LoanAgreement.Status.SIGNED
            # agreement.signed_at = timezone.now()
            # agreement.signed_by = user
            # agreement.save()

            return agreement

        except Exception as e:
            logger.error(
                f"[LoanApplicationTransition] Failed to generate loan agreement: {e}"
            )
            return None
