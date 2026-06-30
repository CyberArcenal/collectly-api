import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.cache import cache

from audit.utils.log import log_audit_event
from debts.models.interest_rate_change_log import InterestRateChangeLog
from notifications.services.notification import NotificationService
from system_settings.utils import email_enabled

logger = logging.getLogger(__name__)


class InterestRateChangeLogStateTransitionService:
    """
    Service for handling interest rate change log transitions.

    Handles events when interest rates are changed.
    Manages notifications and audit logging.
    """

    # ============================================================
    # HELPER METHODS
    # ============================================================

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
    def on_interest_rate_changed(log_entry, user="system", request=None):
        """
        Handle post-interest rate change events.

        Args:
            log_entry: InterestRateChangeLog instance
            user: User who performed the change
            request: HTTP request object for audit
        """
        logger.info(
            f"[InterestRateChangeTransition] on_interest_rate_changed: "
            f"log_id={log_entry.id}, setting_key={log_entry.setting_key}, "
            f"old={log_entry.old_value}, new={log_entry.new_value}, user={user}"
        )

        # 1. Audit log
        log_audit_event(
            request=request,
            user=user,
            action_type='interest_rate_change',
            model_name='InterestRateChangeLog',
            object_id=str(log_entry.id),
            changes={
                'setting_key': log_entry.setting_key,
                'old_value': log_entry.old_value,
                'new_value': log_entry.new_value,
                'reason': log_entry.reason,
            }
        )

        # 2. Notify financial admin (in-app)
        InterestRateChangeLogStateTransitionService._send_in_app_notification(
            title="📊 Interest Rate Changed",
            message=(
                f'Interest rate "{log_entry.setting_key}" has been changed from '
                f'"{log_entry.old_value}" to "{log_entry.new_value}".'
            ),
            metadata={
                'setting_key': log_entry.setting_key,
                'old_value': log_entry.old_value,
                'new_value': log_entry.new_value,
                'log_id': log_entry.id,
            },
            user=user,
        )

        # 3. Send email if enabled
        if email_enabled():
            logger.info(
                f"[InterestRateChangeTransition] Email notification would be sent "
                f"to admin about interest rate change: {log_entry.setting_key}"
            )
            # TODO: Implement email sending to admin using a proper template
            # from email_templates.generic import send_admin_notification_email
            # send_admin_notification_email.delay(
            #     subject=f"Interest Rate Changed: {log_entry.setting_key}",
            #     message=(
            #         f"Interest rate '{log_entry.setting_key}' changed from "
            #         f"{log_entry.old_value} to {log_entry.new_value} by {user}."
            #     ),
            # )

        # 4. If global default rate changed, log for recalculation
        if log_entry.setting_key == "default_interest_rate":
            logger.info(
                "[InterestRateChangeTransition] Global default interest rate changed. "
                "Consider updating active loans if policy requires."
            )

        # 5. Invalidate interest-rate-related caches
        cache.delete_pattern("interest_rate_*")
        logger.info("[InterestRateChangeTransition] Interest rate caches invalidated.")

        logger.info(
            f"[InterestRateChangeTransition] Interest rate change logged: "
            f"{log_entry.setting_key} {log_entry.old_value} → {log_entry.new_value}"
        )