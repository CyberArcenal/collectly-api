import logging
import re
from decimal import Decimal
from datetime import datetime
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from payments.models.payment_transaction import PaymentTransaction
from debts.models.debt import Debt
from notifications.services.notification import NotificationService
from notifications.tasks import send_email_task
from notifications.email_templates.debt_status import generate_paid_email
from system_settings.utils import (
    email_enabled,
    sms_enabled,
    enable_partial_payment,
    get_system_setting,
)

logger = logging.getLogger(__name__)


class PaymentTransactionStateTransitionService:
    """
    Service for handling payment transaction state transitions.

    Handles payment confirmation, voiding, refunding, and amount updates.
    Manages debt balance updates, notifications, and audit logging.
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
                    'type': 'payment_confirmation',
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
        debt = Debt.objects.select_related('borrower').filter(
            id=debt_id,
            deleted_at__isnull=True
        ).first()

        if not debt:
            raise ValidationError({'detail': f'Debt #{debt_id} not found'})

        return debt

    # ============================================================
    # PAYMENT OPERATIONS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def apply_payment(payment, user="system", request=None):
        """
        Apply a payment to its associated debt.

        Args:
            payment: PaymentTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The updated payment instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PaymentTransition] apply_payment: "
            f"payment_id={payment.id}, amount={payment.amount}, "
            f"debt_id={payment.debt_id}, user={user}"
        )

        debt = Debt.objects.filter(id=payment.debt_id).first()
        if not debt:
            raise ValidationError({'detail': 'Payment has no associated debt.'})

        # Store old values for audit
        old_paid_amount = debt.paid_amount
        old_remaining = debt.remaining_amount

        # Apply payment
        debt.paid_amount += payment.amount
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.updated_at = timezone.now()
        debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_apply',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'before': {
                    'paid_amount': float(old_paid_amount),
                    'remaining_amount': float(old_remaining),
                },
                'after': {
                    'paid_amount': float(debt.paid_amount),
                    'remaining_amount': float(debt.remaining_amount),
                },
                'payment_id': payment.id,
                'payment_amount': float(payment.amount),
            }
        )

        logger.info(
            f"[PaymentTransition] Payment #{payment.id} applied. "
            f"Debt #{debt.id} paid_amount={debt.paid_amount}, "
            f"remaining={debt.remaining_amount}"
        )

        return payment

    @staticmethod
    @transaction.atomic
    def reverse_payment(payment, user="system", request=None):
        """
        Reverse a payment from its associated debt.

        Args:
            payment: PaymentTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The updated payment instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PaymentTransition] reverse_payment: "
            f"payment_id={payment.id}, amount={payment.amount}, "
            f"debt_id={payment.debt_id}, user={user}"
        )

        debt = Debt.objects.filter(id=payment.debt_id).first()
        if not debt:
            raise ValidationError({'detail': 'Payment has no associated debt.'})

        # Store old values for audit
        old_paid_amount = debt.paid_amount
        old_remaining = debt.remaining_amount

        # Reverse payment
        debt.paid_amount = max(Decimal('0'), debt.paid_amount - payment.amount)
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.updated_at = timezone.now()
        debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_reverse',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'before': {
                    'paid_amount': float(old_paid_amount),
                    'remaining_amount': float(old_remaining),
                },
                'after': {
                    'paid_amount': float(debt.paid_amount),
                    'remaining_amount': float(debt.remaining_amount),
                },
                'payment_id': payment.id,
                'payment_amount': float(payment.amount),
            }
        )

        logger.info(
            f"[PaymentTransition] Payment #{payment.id} reversed. "
            f"Debt #{debt.id} paid_amount={debt.paid_amount}, "
            f"remaining={debt.remaining_amount}"
        )

        return payment

    @staticmethod
    @transaction.atomic
    def update_payment_amount(payment, old_amount, new_amount, user="system", request=None):
        """
        Update the amount of a payment and adjust debt balance.

        Args:
            payment: PaymentTransaction instance
            old_amount: Old amount
            new_amount: New amount
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The updated payment instance

        Raises:
            ValidationError: If validation fails
        """
        if old_amount == new_amount:
            return payment

        diff = new_amount - old_amount
        logger.info(
            f"[PaymentTransition] update_payment_amount: "
            f"payment_id={payment.id}, old={old_amount}, new={new_amount}, "
            f"diff={diff}, user={user}"
        )

        debt = Debt.objects.filter(id=payment.debt_id).first()
        if not debt:
            raise ValidationError({'detail': 'Payment has no associated debt.'})

        # Store old values for audit
        old_paid_amount = debt.paid_amount
        old_remaining = debt.remaining_amount

        # Adjust debt balances
        debt.paid_amount += Decimal(str(diff))
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.updated_at = timezone.now()
        debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # Update payment amount
        payment.amount = Decimal(str(new_amount))
        payment.save(update_fields=['amount', 'updated_at'])

        # Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_amount_update',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'before': {
                    'paid_amount': float(old_paid_amount),
                    'remaining_amount': float(old_remaining),
                },
                'after': {
                    'paid_amount': float(debt.paid_amount),
                    'remaining_amount': float(debt.remaining_amount),
                },
                'payment_id': payment.id,
                'old_amount': float(old_amount),
                'new_amount': float(new_amount),
                'diff': float(diff),
            }
        )

        logger.info(
            f"[PaymentTransition] Payment #{payment.id} amount updated. "
            f"Debt #{debt.id} paid_amount={debt.paid_amount}, "
            f"remaining={debt.remaining_amount}"
        )

        return payment

    @staticmethod
    @transaction.atomic
    def transfer_payment(payment, old_debt_id, new_debt_id, user="system", request=None):
        """
        Transfer a payment from one debt to another.

        Args:
            payment: PaymentTransaction instance
            old_debt_id: Old debt ID
            new_debt_id: New debt ID
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The transferred payment instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PaymentTransition] transfer_payment: "
            f"payment_id={payment.id}, old_debt={old_debt_id}, "
            f"new_debt={new_debt_id}, user={user}"
        )

        old_debt = Debt.objects.filter(id=old_debt_id).first()
        new_debt = Debt.objects.filter(id=new_debt_id).first()

        if not old_debt or not new_debt:
            raise ValidationError({'detail': 'Old or new debt not found.'})

        # Store old values for audit
        old_debt_old_paid = old_debt.paid_amount
        old_debt_old_remaining = old_debt.remaining_amount
        new_debt_old_paid = new_debt.paid_amount
        new_debt_old_remaining = new_debt.remaining_amount

        # Remove from old debt
        old_debt.paid_amount = max(Decimal('0'), old_debt.paid_amount - payment.amount)
        old_debt.remaining_amount = old_debt.total_amount - old_debt.paid_amount
        if old_debt.remaining_amount < 0:
            old_debt.remaining_amount = Decimal('0')
        old_debt.updated_at = timezone.now()
        old_debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # Add to new debt
        new_debt.paid_amount += payment.amount
        new_debt.remaining_amount = new_debt.total_amount - new_debt.paid_amount
        if new_debt.remaining_amount < 0:
            new_debt.remaining_amount = Decimal('0')
        new_debt.updated_at = timezone.now()
        new_debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # Update payment debt reference
        payment.debt = new_debt
        payment.updated_at = timezone.now()
        payment.save(update_fields=['debt', 'updated_at'])

        # Audit logs
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_transfer_remove',
            model_name='Debt',
            object_id=str(old_debt.id),
            changes={
                'before': {
                    'paid_amount': float(old_debt_old_paid),
                    'remaining_amount': float(old_debt_old_remaining),
                },
                'after': {
                    'paid_amount': float(old_debt.paid_amount),
                    'remaining_amount': float(old_debt.remaining_amount),
                },
                'payment_id': payment.id,
                'payment_amount': float(payment.amount),
            }
        )

        log_audit_event(
            request=request,
            user=user,
            action_type='payment_transfer_add',
            model_name='Debt',
            object_id=str(new_debt.id),
            changes={
                'before': {
                    'paid_amount': float(new_debt_old_paid),
                    'remaining_amount': float(new_debt_old_remaining),
                },
                'after': {
                    'paid_amount': float(new_debt.paid_amount),
                    'remaining_amount': float(new_debt.remaining_amount),
                },
                'payment_id': payment.id,
                'payment_amount': float(payment.amount),
            }
        )

        logger.info(
            f"[PaymentTransition] Payment #{payment.id} transferred. "
            f"Old debt #{old_debt_id} paid_amount={old_debt.paid_amount}, "
            f"New debt #{new_debt_id} paid_amount={new_debt.paid_amount}"
        )

        return payment

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_confirm(payment, user="system", request=None):
        """
        Handle payment confirmation.

        Args:
            payment: PaymentTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The confirmed payment instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[PaymentTransition] on_confirm: payment_id={payment.id}, user={user}")

        # 1. Apply payment to debt
        PaymentTransactionStateTransitionService.apply_payment(payment, user, request)

        # 2. Reload debt with borrower
        debt_with_borrower = PaymentTransactionStateTransitionService._get_debt_with_borrower(
            payment.debt_id
        )

        # 3. Check partial payment setting
        allow_partial = enable_partial_payment()
        remaining_after = debt_with_borrower.remaining_amount

        if not allow_partial and remaining_after > Decimal('0.01'):
            # Reverse the payment
            PaymentTransactionStateTransitionService.reverse_payment(payment, user, request)
            raise ValidationError({
                'detail': 'Partial payments are disabled. You can only pay the full remaining amount.'
            })

        # 4. If debt is fully paid, mark status as 'paid'
        if remaining_after <= Decimal('0.01') and debt_with_borrower.status != Debt.Status.PAID:
            old_status = debt_with_borrower.status
            debt_with_borrower.status = Debt.Status.PAID
            debt_with_borrower.updated_at = timezone.now()
            debt_with_borrower.save(update_fields=['status', 'updated_at'])

            logger.info(
                f"[PaymentTransition] Debt #{debt_with_borrower.id} fully paid, "
                f"status updated to 'paid'"
            )

            # Audit log for debt status change
            log_audit_event(
                request=request,
                user=user,
                action_type='debt_paid',
                model_name='Debt',
                object_id=str(debt_with_borrower.id),
                changes={
                    'before': {'status': old_status},
                    'after': {'status': Debt.Status.PAID},
                }
            )

        # 5. Mark payment as confirmed
        payment.confirmed = True
        payment.updated_at = timezone.now()
        payment.save(update_fields=['confirmed', 'updated_at'])

        # 6. In-app notification (for admin)
        PaymentTransactionStateTransitionService._send_in_app_notification(
            title="💳 Payment Confirmed",
            message=(
                f'Payment of ₱{payment.amount:,.2f} for debt '
                f'"{debt_with_borrower.name}" has been confirmed.'
            ),
            metadata={
                'payment_id': payment.id,
                'debt_id': debt_with_borrower.id,
                'amount': float(payment.amount),
                'borrower_id': debt_with_borrower.borrower_id,
            },
            user=user,
        )

        # 7. Send email to debtor using centralized template
        can_send_email = email_enabled()
        borrower = debt_with_borrower.borrower

        if can_send_email and borrower and borrower.email:
            try:
                email_data = PaymentTransactionStateTransitionService._get_email_data()

                # Generate email using the same template as debt paid
                # This matches the original Node.js behavior using generatePaidEmail
                html = generate_paid_email({
                    'debt_id': debt_with_borrower.id,
                    'debtor_name': borrower.name,
                    'original_amount': debt_with_borrower.total_amount,
                    'total_paid': payment.amount,
                    **email_data,
                })

                PaymentTransactionStateTransitionService._send_email(
                    recipient=borrower.email,
                    subject="✅ Payment Confirmed – Thank You!",
                    html=html,
                    user=user,
                )

                logger.info(
                    f"[PaymentTransition] Payment confirmation email sent to {borrower.email}"
                )
            except Exception as e:
                logger.error(f"[PaymentTransition] Failed to send payment confirmation email: {e}")

        # 8. Send SMS to debtor
        can_send_sms = sms_enabled()
        if can_send_sms and borrower and borrower.contact:
            try:
                message = (
                    f"Dear {borrower.name}, your payment of ₱{payment.amount:,.2f} "
                    f'for debt "{debt_with_borrower.name}" has been confirmed. '
                    f"Remaining balance: ₱{debt_with_borrower.remaining_amount:,.2f}. "
                    f"Thank you!"
                )
                PaymentTransactionStateTransitionService._send_sms(
                    phone_number=borrower.contact,
                    message=message,
                    user=user,
                )

                logger.info(
                    f"[PaymentTransition] Payment confirmation SMS sent to {borrower.contact}"
                )
            except Exception as e:
                logger.error(f"[PaymentTransition] Failed to send payment confirmation SMS: {e}")

        # 9. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_confirm',
            model_name='PaymentTransaction',
            object_id=str(payment.id),
            changes={
                'before': {'confirmed': False},
                'after': {'confirmed': True},
            }
        )

        logger.info(f"[PaymentTransition] Payment #{payment.id} confirmed")
        return payment

    @staticmethod
    @transaction.atomic
    def on_void(payment, user="system", request=None):
        """
        Handle payment voiding.

        Args:
            payment: PaymentTransaction instance
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The voided payment instance

        Raises:
            ValidationError: If validation fails
        """
        logger.info(f"[PaymentTransition] on_void: payment_id={payment.id}, user={user}")

        if payment.deleted_at:
            raise ValidationError({'detail': 'Payment is already voided.'})

        # 1. Reverse payment from debt
        PaymentTransactionStateTransitionService.reverse_payment(payment, user, request)

        # 2. Soft delete payment
        payment.soft_delete()

        # 3. In-app notification to debtor
        debt_with_borrower = PaymentTransactionStateTransitionService._get_debt_with_borrower(
            payment.debt_id
        )

        if debt_with_borrower.borrower:
            PaymentTransactionStateTransitionService._send_in_app_notification(
                title="🔄 Payment Voided",
                message=(
                    f'Your payment of ₱{payment.amount:,.2f} for debt '
                    f'"{debt_with_borrower.name}" has been voided.'
                ),
                metadata={
                    'payment_id': payment.id,
                    'debt_id': debt_with_borrower.id,
                    'amount': float(payment.amount),
                },
                user=user,
            )

        # 4. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_void',
            model_name='PaymentTransaction',
            object_id=str(payment.id),
            changes={
                'before': {'status': 'active', 'deleted_at': None},
                'after': {'status': 'voided', 'deleted_at': payment.deleted_at},
            }
        )

        logger.info(f"[PaymentTransition] Payment #{payment.id} voided")
        return payment

    @staticmethod
    @transaction.atomic
    def on_refund(payment, refund_amount, user="system", request=None):
        """
        Handle payment refund.

        Args:
            payment: PaymentTransaction instance
            refund_amount: Amount to refund
            user: User performing the action
            request: HTTP request object for audit

        Returns:
            PaymentTransaction: The refund transaction created

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[PaymentTransition] on_refund: payment_id={payment.id}, "
            f"refund_amount={refund_amount}, user={user}"
        )

        if refund_amount <= 0:
            raise ValidationError({'detail': 'Refund amount must be positive.'})

        if refund_amount > payment.amount:
            raise ValidationError({
                'detail': f'Refund amount cannot exceed payment amount (₱{payment.amount:,.2f}).'
            })

        debt = Debt.objects.filter(id=payment.debt_id).first()
        if not debt:
            raise ValidationError({'detail': 'Payment has no associated debt.'})

        # 1. Create refund transaction (negative amount)
        refund = PaymentTransaction.objects.create(
            debt=debt,
            method=payment.method,
            amount=-refund_amount,
            payment_date=timezone.now().date(),
            reference=f"Refund for #{payment.id}",
            notes=f"Refund processed by {user}",
            recorded_at=timezone.now(),
            recorded_by=None,
        )

        # 2. Adjust debt balances
        old_paid_amount = debt.paid_amount
        old_remaining = debt.remaining_amount

        debt.paid_amount = max(Decimal('0'), debt.paid_amount - refund_amount)
        debt.remaining_amount = debt.total_amount - debt.paid_amount
        if debt.remaining_amount < 0:
            debt.remaining_amount = Decimal('0')
        debt.updated_at = timezone.now()
        debt.save(update_fields=['paid_amount', 'remaining_amount', 'updated_at'])

        # 3. In-app notification to debtor
        debt_with_borrower = PaymentTransactionStateTransitionService._get_debt_with_borrower(
            debt.id
        )

        if debt_with_borrower.borrower:
            PaymentTransactionStateTransitionService._send_in_app_notification(
                title="💰 Payment Refunded",
                message=(
                    f'A refund of ₱{refund_amount:,.2f} has been issued for your '
                    f'payment of ₱{payment.amount:,.2f} on debt "{debt_with_borrower.name}".'
                ),
                metadata={
                    'payment_id': payment.id,
                    'refund_id': refund.id,
                    'debt_id': debt.id,
                    'refund_amount': float(refund_amount),
                },
                user=user,
            )

        # 4. Audit logs
        log_audit_event(
            request=request,
            user=user,
            action_type='payment_refund',
            model_name='PaymentTransaction',
            object_id=str(refund.id),
            changes={
                'original_payment_id': payment.id,
                'refund_amount': float(refund_amount),
                'amount': float(refund.amount),
                'debt_id': debt.id,
            }
        )

        log_audit_event(
            request=request,
            user=user,
            action_type='debt_refund',
            model_name='Debt',
            object_id=str(debt.id),
            changes={
                'before': {'paid_amount': float(old_paid_amount)},
                'after': {'paid_amount': float(debt.paid_amount)},
                'refund_amount': float(refund_amount),
            }
        )

        logger.info(
            f"[PaymentTransition] Refund created: #{refund.id} for payment #{payment.id}. "
            f"Debt #{debt.id} paid_amount={debt.paid_amount}"
        )

        return refund