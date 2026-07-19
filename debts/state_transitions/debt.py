import logging
import re
from decimal import Decimal
from datetime import datetime, date
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from debts.models.forgiveness_log import ForgivenessLog
from notifications.models.notification_log import NotificationLog
from notifications.services.notification_log import NotificationLogService
from payments.models.penalty_transaction import PenaltyTransaction
from notifications.models.notification import Notification
from notifications.services.notification import NotificationService
from notifications.email_templates.debt_status import (
    generate_paid_email,
    generate_overdue_email,
    generate_defaulted_email,
    generate_restored_email,
    generate_forgiveness_email,
)
from system_settings.utils import (
    enable_auto_penalty,
    default_penalty_rate,
    penalty_calculation_method,
    penalty_grace_days,
    email_enabled,
    sms_enabled,
    allowed_loan_statuses,
    get_system_setting,
)
from borrowers.services.credit_check import CreditCheckService

logger = logging.getLogger(__name__)


class DebtStateTransitionService:
    """
    Service for handling debt state transitions.

    Handles debt status changes: paid, overdue, defaulted, restored, and forgiveness.
    Manages notifications, email/SMS alerts, penalties, and credit score updates.
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
            NotificationLogService.create(
                data={
                    "channel": NotificationLog.Channel.EMAIL,
                    "recipient": recipient,
                    "subject": subject,
                    "payload": html,
                },
                user=user,
                request=None,
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

        try:
            NotificationLogService.create(
                data={
                    "channel": NotificationLog.Channel.SMS,
                    "recipient": phone_number,
                    "payload": message,
                },
                user=user,
                request=None,
            )
            logger.info(f"[SMS] Queued email to {phone_number}: {message}")
            return True
        except Exception as e:
            logger.error(f"[SMS] Failed to queue email to {phone_number}: {e}")
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
        debt = Debt.objects.select_related("borrower").filter(id=debt_id).first()
        if not debt:
            raise ValidationError({"detail": f"Debt #{debt_id} not found"})
        return debt

    @staticmethod
    def _update_credit_score(borrower_id, user="system"):
        """
        Update credit score for a borrower.

        Args:
            borrower_id: Borrower ID
            user: User performing the action
        """
        try:
            if borrower_id:
                CreditCheckService.perform_credit_check(
                    data={"debtor_id": borrower_id}, user=user, request=None
                )
                logger.info(
                    f"[DebtTransition] Credit score updated for borrower #{borrower_id}"
                )
        except Exception as e:
            logger.warning(f"[DebtTransition] Failed to update credit score: {e}")

    @staticmethod
    def _is_status_allowed(new_status):
        """
        Check if a status is allowed by system settings.

        Args:
            new_status: Status to check

        Returns:
            bool: True if allowed
        """
        allowed = allowed_loan_statuses()
        return new_status in allowed

    @staticmethod
    def _mark_notifications_as_read(debt_id):
        """
        Mark all unread notifications for a debt as read.

        Args:
            debt_id: Debt ID
        """
        Notification.objects.filter(
            debt_id=debt_id, is_read=False, deleted_at__isnull=True
        ).update(is_read=True, updated_at=timezone.now())

        logger.info(
            f"[DebtTransition] Marked notifications as read for debt #{debt_id}"
        )

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_paid(debt, user="system", request=None):
        """
        Mark a debt as paid.

        Args:
            debt: Debt instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[DebtTransition] on_paid: debt_id={debt.id}, user={user}")

        # Check if status is allowed
        if not DebtStateTransitionService._is_status_allowed(Debt.Status.PAID):
            raise ValidationError(
                {
                    "detail": f"Status {Debt.Status.PAID} is not allowed by system settings."
                }
            )

        # Reload debt with borrower
        debt_with_borrower = DebtStateTransitionService._get_debt_with_borrower(debt.id)

        # Store old status for audit
        old_status = debt_with_borrower.status

        # Update debt status to paid
        debt_with_borrower.status = Debt.Status.PAID
        debt_with_borrower.updated_at = timezone.now()
        debt_with_borrower.save()

        # Mark all unread notifications as read
        DebtStateTransitionService._mark_notifications_as_read(debt.id)

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_paid",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "before": {"status": old_status},
                "after": {"status": Debt.Status.PAID},
            },
        )

        # Update credit score
        if debt_with_borrower.borrower_id:
            DebtStateTransitionService._update_credit_score(
                debt_with_borrower.borrower_id, user=user
            )

        # Send notifications
        borrower = debt_with_borrower.borrower

        if borrower:
            # In-app notification
            DebtStateTransitionService._send_in_app_notification(
                title="✅ Debt Fully Paid",
                message=f'Debt "{debt_with_borrower.name}" has been fully paid.',
                metadata={"debt_id": debt.id, "borrower_id": borrower.id},
                user=user,
            )

            # Email notification
            if email_enabled() and borrower.email:
                email_data = DebtStateTransitionService._get_email_data()
                html = generate_paid_email(
                    {
                        "debt_id": debt.id,
                        "debtor_name": borrower.name,
                        "original_amount": debt_with_borrower.total_amount,
                        "total_paid": debt_with_borrower.total_amount,
                        **email_data,
                    }
                )
                DebtStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="✅ Debt Fully Paid",
                    html=html,
                    user=user,
                )

            # SMS notification
            if sms_enabled() and borrower.contact:
                message = f'Dear {borrower.name}, your debt "{debt_with_borrower.name}" is fully paid. Thank you!'
                DebtStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

        logger.info(f"[DebtTransition] Debt #{debt.id} marked as paid")
        return debt_with_borrower

    @staticmethod
    @transaction.atomic
    def on_overdue(debt, user="system", request=None):
        """
        Mark a debt as overdue and apply auto-penalty if enabled.

        Args:
            debt: Debt instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[DebtTransition] on_overdue: debt_id={debt.id}, user={user}")

        # Check if status is allowed
        if not DebtStateTransitionService._is_status_allowed(Debt.Status.OVERDUE):
            raise ValidationError(
                {
                    "detail": f"Status {Debt.Status.OVERDUE} is not allowed by system settings."
                }
            )

        # Reload debt with borrower
        debt_with_borrower = DebtStateTransitionService._get_debt_with_borrower(debt.id)

        # Store old status for audit
        old_status = debt_with_borrower.status

        # Update debt status to overdue
        debt_with_borrower.status = Debt.Status.OVERDUE
        debt_with_borrower.updated_at = timezone.now()
        debt_with_borrower.save()

        # Apply auto-penalty
        penalty_amount = Decimal("0")
        if enable_auto_penalty():
            penalty_amount = DebtStateTransitionService._apply_auto_penalty(
                debt=debt_with_borrower,
                user=user,
            )

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_overdue",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "before": {"status": old_status},
                "after": {"status": Debt.Status.OVERDUE},
                "penalty_applied": float(penalty_amount),
            },
        )

        # Send notifications
        borrower = debt_with_borrower.borrower

        if borrower:
            # In-app notification
            DebtStateTransitionService._send_in_app_notification(
                title="⏰ Debt Overdue",
                message=f'Debt "{debt_with_borrower.name}" is now overdue. Please make a payment.',
                metadata={
                    "debt_id": debt.id,
                    "borrower_id": borrower.id,
                    "days_overdue": debt_with_borrower.days_overdue,
                },
                user=user,
            )

            # Email notification
            if email_enabled() and borrower.email:
                email_data = DebtStateTransitionService._get_email_data()
                html = generate_overdue_email(
                    {
                        "debt_id": debt.id,
                        "debtor_name": borrower.name,
                        "original_amount": debt_with_borrower.total_amount,
                        "remaining_balance": debt_with_borrower.remaining_amount,
                        "due_date": debt_with_borrower.due_date,
                        "days_overdue": (
                            (timezone.now().date() - debt_with_borrower.due_date).days
                            if debt_with_borrower.due_date
                            else 0
                        ),
                        "penalty_amount": float(penalty_amount),
                        **email_data,
                    }
                )
                DebtStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="⏰ Debt Overdue – Immediate Action Required",
                    html=html,
                    user=user,
                )

            # SMS notification
            if sms_enabled() and borrower.contact:
                message = f'Dear {borrower.name}, your payment for debt "{debt_with_borrower.name}" is overdue.'
                DebtStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

        logger.info(f"[DebtTransition] Debt #{debt.id} marked as overdue")
        return debt_with_borrower

    @staticmethod
    def _apply_auto_penalty(debt, user="system"):
        """
        Apply auto-penalty to an overdue debt.

        Args:
            debt: Debt instance
            user: User performing the action

        Returns:
            Decimal: Penalty amount applied
        """
        grace_days = penalty_grace_days()
        due_date = debt.due_date
        today = timezone.now().date()

        # Check if debt is beyond grace period
        if due_date:
            days_overdue = (today - due_date).days
            if days_overdue <= grace_days:
                logger.info(
                    f"[DebtTransition] Debt #{debt.id} within grace period ({days_overdue} days)"
                )
                return Decimal("0")

            # Check if penalty already exists since due date
            existing_penalty = PenaltyTransaction.objects.filter(
                debt=debt, deleted_at__isnull=True, penalty_date__gte=due_date
            ).exists()

            if existing_penalty:
                logger.info(
                    f"[DebtTransition] Penalty already exists for debt #{debt.id}"
                )
                return Decimal("0")

            # Calculate penalty
            penalty_rate = debt.penalty_rate or default_penalty_rate()
            calc_method = penalty_calculation_method()

            if calc_method == "percentage":
                penalty_amount = debt.remaining_amount * (
                    Decimal(str(penalty_rate)) / Decimal("100")
                )
            else:  # fixed
                penalty_amount = Decimal(str(penalty_rate))

            if penalty_amount > 0:
                # Create penalty
                PenaltyTransaction.objects.create(
                    debt=debt,
                    amount=penalty_amount,
                    penalty_date=today,
                    reason=f"Auto-penalty for overdue ({days_overdue} days)",
                    is_auto=True,
                )

                # Update debt remaining amount
                debt.remaining_amount += penalty_amount
                debt.save()

                logger.info(
                    f"[DebtTransition] Applied penalty of {penalty_amount} to debt #{debt.id}"
                )

                # Audit log for penalty
                log_audit_event(
                    request=None,
                    user="system",
                    action_type="penalty_auto_applied",
                    model_name="PenaltyTransaction",
                    object_id=str(debt.id),
                    changes={
                        "amount": float(penalty_amount),
                        "days_overdue": days_overdue,
                        "reason": "Auto-penalty",
                    },
                )

                return penalty_amount

        return Decimal("0")

    @staticmethod
    @transaction.atomic
    def on_defaulted(debt, user="system", request=None):
        """
        Mark a debt as defaulted.

        Args:
            debt: Debt instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[DebtTransition] on_defaulted: debt_id={debt.id}, user={user}")

        # Check if status is allowed
        if not DebtStateTransitionService._is_status_allowed(Debt.Status.DEFAULTED):
            raise ValidationError(
                {
                    "detail": f"Status {Debt.Status.DEFAULTED} is not allowed by system settings."
                }
            )

        # Reload debt with borrower
        debt_with_borrower = DebtStateTransitionService._get_debt_with_borrower(debt.id)

        # Store old status for audit
        old_status = debt_with_borrower.status

        # Update debt status to defaulted
        debt_with_borrower.status = Debt.Status.DEFAULTED
        debt_with_borrower.updated_at = timezone.now()
        debt_with_borrower.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_defaulted",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "before": {"status": old_status},
                "after": {"status": Debt.Status.DEFAULTED},
            },
        )

        # Send notifications
        borrower = debt_with_borrower.borrower

        if borrower:
            # In-app notification for borrower
            DebtStateTransitionService._send_in_app_notification(
                title="⚠️ Debt Defaulted",
                message=f'Debt "{debt_with_borrower.name}" is now in default. Please contact support.',
                metadata={"debt_id": debt.id, "borrower_id": borrower.id},
                user=user,
            )

            # Internal admin notification
            DebtStateTransitionService._send_in_app_notification(
                title="⚠️ Debt Defaulted – Legal Action Required",
                message=f'Debt #{debt.id} ({debt_with_borrower.name}) for borrower {borrower.name or "Unknown"} has been defaulted. Please review.',
                metadata={"debt_id": debt.id, "borrower_id": borrower.id},
                user=user,
            )

            # Email notification
            if email_enabled() and borrower.email:
                email_data = DebtStateTransitionService._get_email_data()
                html = generate_defaulted_email(
                    {
                        "debt_id": debt.id,
                        "debtor_name": borrower.name,
                        "original_amount": debt_with_borrower.total_amount,
                        "remaining_balance": debt_with_borrower.remaining_amount,
                        "due_date": debt_with_borrower.due_date,
                        **email_data,
                    }
                )
                DebtStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="⚠️ Debt Defaulted – Legal Action Pending",
                    html=html,
                    user=user,
                )

            # SMS notification
            if sms_enabled() and borrower.contact:
                message = f'Dear {borrower.name}, your debt "{debt_with_borrower.name}" is now in default.'
                DebtStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

        logger.info(f"[DebtTransition] Debt #{debt.id} marked as defaulted")
        return debt_with_borrower

    @staticmethod
    @transaction.atomic
    def on_restore_to_active(debt, user="system", request=None):
        """
        Restore a debt to active status.

        Args:
            debt: Debt instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            Debt: The updated debt instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[DebtTransition] on_restore_to_active: debt_id={debt.id}, user={user}"
        )

        # Check if status is allowed
        if not DebtStateTransitionService._is_status_allowed(Debt.Status.ACTIVE):
            raise ValidationError(
                {
                    "detail": f"Status {Debt.Status.ACTIVE} is not allowed by system settings."
                }
            )

        # Reload debt with borrower
        debt_with_borrower = DebtStateTransitionService._get_debt_with_borrower(debt.id)

        # Store old status for audit
        old_status = debt_with_borrower.status

        # Update debt status to active
        debt_with_borrower.status = Debt.Status.ACTIVE
        debt_with_borrower.updated_at = timezone.now()
        debt_with_borrower.save()

        # Recalculate remaining amount
        remaining = debt_with_borrower.total_amount - debt_with_borrower.paid_amount
        if remaining != debt_with_borrower.remaining_amount:
            debt_with_borrower.remaining_amount = remaining
            debt_with_borrower.save()

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_restore_to_active",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "before": {"status": old_status},
                "after": {"status": Debt.Status.ACTIVE},
            },
        )

        # Send notifications
        borrower = debt_with_borrower.borrower

        if borrower:
            # In-app notification
            DebtStateTransitionService._send_in_app_notification(
                title="↺ Debt Restored",
                message=f'Debt "{debt_with_borrower.name}" has been restored to active status.',
                metadata={"debt_id": debt.id, "borrower_id": borrower.id},
                user=user,
            )

            # Email notification
            if email_enabled() and borrower.email:
                email_data = DebtStateTransitionService._get_email_data()
                html = generate_restored_email(
                    {
                        "debt_id": debt.id,
                        "debtor_name": borrower.name,
                        "original_amount": debt_with_borrower.total_amount,
                        "remaining_balance": debt_with_borrower.remaining_amount,
                        "due_date": debt_with_borrower.due_date,
                        **email_data,
                    }
                )
                DebtStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="↺ Debt Restored – Payments Resumed",
                    html=html,
                    user=user,
                )

            # SMS notification
            if sms_enabled() and borrower.contact:
                message = f'Dear {borrower.name}, your debt "{debt_with_borrower.name}" is now active again.'
                DebtStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

        logger.info(f"[DebtTransition] Debt #{debt.id} restored to active")
        return debt_with_borrower

    @staticmethod
    @transaction.atomic
    def on_forgiveness(debt, amount_forgiven, user="system", reason=None, request=None):
        """
        Apply debt forgiveness.

        Args:
            debt: Debt instance
            amount_forgiven: Amount to forgive
            user: User performing the action
            reason: Reason for forgiveness
            request: HTTP request object for audit

        Returns:
            None

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[DebtTransition] on_forgiveness: debt_id={debt.id}, amount={amount_forgiven}, user={user}"
        )

        # Check if status is allowed
        if not DebtStateTransitionService._is_status_allowed(debt.status):
            raise ValidationError(
                {"detail": f"Status {debt.status} is not allowed by system settings."}
            )

        # Reload debt with borrower
        debt_with_borrower = DebtStateTransitionService._get_debt_with_borrower(debt.id)

        # Store old values for audit
        old_total = debt_with_borrower.total_amount
        old_remaining = debt_with_borrower.remaining_amount

        # Audit log
        note = reason or "Debt forgiveness applied"
        log_audit_event(
            request=request,
            user=user,
            action_type="debt_forgiveness",
            model_name="Debt",
            object_id=str(debt.id),
            changes={
                "forgiveness_amount": float(amount_forgiven),
                "reason": note,
                "before_total": float(old_total),
                "after_total": float(debt_with_borrower.total_amount),
            },
        )

        # Send notifications
        borrower = debt_with_borrower.borrower

        if borrower:
            # In-app notification
            DebtStateTransitionService._send_in_app_notification(
                title="✓ Debt Forgiveness Applied",
                message=(
                    f"An amount of {amount_forgiven:.2f} has been forgiven from debt "
                    f'"{debt_with_borrower.name}". Remaining balance: {debt_with_borrower.remaining_amount:.2f}.'
                ),
                metadata={
                    "debt_id": debt.id,
                    "borrower_id": borrower.id,
                    "amount_forgiven": float(amount_forgiven),
                },
                user=user,
            )

            # Email notification
            if email_enabled() and borrower.email:
                email_data = DebtStateTransitionService._get_email_data()
                html = generate_forgiveness_email(
                    {
                        "debt_id": debt.id,
                        "debtor_name": borrower.name,
                        "original_amount": debt_with_borrower.total_amount,
                        "forgiven_amount": float(amount_forgiven),
                        "new_balance": debt_with_borrower.remaining_amount,
                        "reason": note,
                        **email_data,
                    }
                )
                DebtStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="✓ Debt Forgiveness Applied",
                    html=html,
                    user=user,
                )

            # SMS notification
            if sms_enabled() and borrower.contact:
                message = (
                    f"Dear {borrower.name}, {amount_forgiven:.2f} forgiven from debt "
                    f'"{debt_with_borrower.name}". New balance: {debt_with_borrower.remaining_amount:.2f}.'
                )
                DebtStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

        logger.info(
            f"[DebtTransition] Forgiveness applied to debt #{debt.id}: {amount_forgiven}"
        )

    @staticmethod
    @transaction.atomic
    def recalculate_balance(debt, user="system", request=None, update_status=True):
        """
        Recalculate remaining amount based on total_amount and paid_amount.
        Optionally update status to PAID if remaining <= 0.

        Args:
            debt: Debt instance
            user: User performing the action (for audit)
            request: HTTP request object (for audit)
            update_status: If True, automatically mark as PAID when fully paid

        Returns:
            Debt: The updated debt instance
        """
        logger.info(
            f"[DebtTransition] recalculate_balance: debt_id={debt.id}, user={user}"
        )

        # Reload from DB to ensure latest values
        debt.refresh_from_db()

        old_remaining = debt.remaining_amount
        new_remaining = debt.total_amount - debt.paid_amount
        if new_remaining < 0:
            new_remaining = Decimal("0")

        # Update if changed
        if new_remaining != debt.remaining_amount:
            debt.remaining_amount = new_remaining
            debt.updated_at = timezone.now()
            debt.save(update_fields=["remaining_amount", "updated_at"])

            logger.info(
                f"[DebtTransition] Debt #{debt.id} remaining updated: "
                f"{old_remaining:.2f} → {new_remaining:.2f}"
            )

            # Audit log for balance change
            log_audit_event(
                request=request,
                user=user,
                action_type="debt_balance_recalc",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": {"remaining_amount": float(old_remaining)},
                    "after": {"remaining_amount": float(new_remaining)},
                    "recalculated_by": str(user) if user else "system",
                },
            )

        # If fully paid and update_status is True, mark as PAID
        if (
            update_status
            and new_remaining <= Decimal("0.01")
            and debt.status != Debt.Status.PAID
        ):
            logger.info(
                f"[DebtTransition] Debt #{debt.id} fully paid, updating status to PAID"
            )
            old_status = debt.status
            debt.status = Debt.Status.PAID
            debt.updated_at = timezone.now()
            debt.save(update_fields=["status", "updated_at"])

            log_audit_event(
                request=request,
                user=user,
                action_type="debt_status_update",
                model_name="Debt",
                object_id=str(debt.id),
                changes={
                    "before": {"status": old_status},
                    "after": {"status": Debt.Status.PAID},
                    "reason": "fully paid",
                },
            )

            # Trigger on_paid for notifications, etc.
            # But careful not to loop - we can call on_paid directly or rely on signal
            # Since on_paid is already called by the signal when status changes, we can let that handle it.
            # However, we might want to explicitly send notifications here, but the signal will catch it.
            # For simplicity, we can call the signal method or just let the post_save signal handle it.
            # We'll just update the status and let the signal's post_save trigger the transition.

        return debt
