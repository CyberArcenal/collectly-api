# Add this to  borrowers/state_transition/credit_check.py

import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import models
from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from borrowers.models.credit_check_log import CreditCheckLog
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


class CreditCheckStateTransitionService:
    """
    Service for handling credit check state transitions.
    
    Handles events when credit checks are performed or deleted.
    Manages notifications, audit logging, and optional credit rating updates.
    """

    # ============================================================
    # CONSTANTS
    # ============================================================

    # Threshold for poor credit score alerts
    POOR_SCORE_THRESHOLD = 500

    # Credit rating mappings
    RATING_EXCELLENT = "Excellent"
    RATING_GOOD = "Good"
    RATING_FAIR = "Fair"
    RATING_POOR = "Poor"

    # ============================================================
    # STATE TRANSITION METHODS
    # ============================================================

    @staticmethod
    @transaction.atomic
    def on_check_performed(log_entry, user="system", request=None):
        """
        Handle post-credit check events.

        Args:
            log_entry: CreditCheckLog instance
            user: User who performed the check
            request: HTTP request object for audit

        Returns:
            CreditCheckLog: The log entry (unchanged)

        Raises:
            ValidationError: If validation fails
        """
        logger.info(
            f"[CreditCheckTransition] on_check_performed: "
            f"log_id={log_entry.id}, debtor_id={log_entry.debtor_id}, "
            f"score={log_entry.score}, risk_level={log_entry.risk_level}, user={user}"
        )

        # 1. Audit log - already saved in DB, but add explicit audit entry
        log_audit_event(
            request=request,
            user=user,
            action_type='credit_check_performed',
            model_name='CreditCheckLog',
            object_id=str(log_entry.id),
            changes={
                'debtor_id': log_entry.debtor_id,
                'score': log_entry.score,
                'risk_level': log_entry.risk_level,
                'remarks': log_entry.remarks,
            }
        )

        # 2. If score is poor, send alert notification to credit officer
        if log_entry.score < CreditCheckStateTransitionService.POOR_SCORE_THRESHOLD:
            CreditCheckStateTransitionService._send_poor_score_alert(
                log_entry=log_entry,
                user=user,
            )

        # 3. Update debtor's credit rating (optional)
        CreditCheckStateTransitionService._update_borrower_credit_rating(
            log_entry=log_entry,
            user=user,
        )

        return log_entry

    @staticmethod
    @transaction.atomic
    def on_log_deleted(log_entry, user="system", request=None):
        """
        Handle post-credit check log deletion events.

        Args:
            log_entry: CreditCheckLog instance (before deletion)
            user: User who deleted the log
            request: HTTP request object for audit

        Returns:
            None
        """
        logger.info(
            f"[CreditCheckTransition] on_log_deleted: "
            f"log_id={log_entry.id}, debtor_id={log_entry.debtor_id}, "
            f"score={log_entry.score}, user={user}"
        )

        # 1. Audit trail - record deletion
        log_audit_event(
            request=request,
            user=user,
            action_type='credit_check_delete',
            model_name='CreditCheckLog',
            object_id=str(log_entry.id),
            changes={
                'deleted': True,
                'debtor_id': log_entry.debtor_id,
                'score': log_entry.score,
                'risk_level': log_entry.risk_level,
                'reason': 'Credit check log deleted',
            }
        )

        # 2. Notify compliance officer (optional)
        CreditCheckStateTransitionService._send_deletion_notification(
            log_entry=log_entry,
            user=user,
        )

        # 3. Recalculate borrower's credit rating based on remaining logs
        CreditCheckStateTransitionService._recalculate_borrower_rating(
            log_entry=log_entry,
            user=user,
        )

        logger.info(
            f"[CreditCheckTransition] Credit check log #{log_entry.id} "
            f"deleted by {user}"
        )

    # ============================================================
    # HELPER METHODS
    # ============================================================

    @staticmethod
    def _send_poor_score_alert(log_entry, user="system"):
        """
        Send alert notification for poor credit score.

        Args:
            log_entry: CreditCheckLog instance
            user: User performing the action
        """
        try:
            # Get debtor name for better message
            debtor = Borrower.objects.filter(
                id=log_entry.debtor_id,
                deleted_at__isnull=True
            ).first()

            debtor_name = debtor.name if debtor else f"ID {log_entry.debtor_id}"

            NotificationService.create(
                data={
                    'title': '⚠️ Poor Credit Score Alert',
                    'message': (
                        f'Debtor "{debtor_name}" has a credit score of '
                        f'{log_entry.score} ({log_entry.risk_level}). '
                        f'Please review.'
                    ),
                    'type': 'error',
                    'metadata': {
                        'debtor_id': log_entry.debtor_id,
                        'score': log_entry.score,
                        'risk_level': log_entry.risk_level,
                        'log_id': log_entry.id,
                    },
                },
                user=user,
                request=None
            )

            logger.info(
                f"[CreditCheckTransition] Poor score alert sent for "
                f"debtor #{log_entry.debtor_id}: score={log_entry.score}"
            )

        except Exception as e:
            logger.error(
                f"[CreditCheckTransition] Failed to send credit score alert: {e}"
            )

    @staticmethod
    def _send_deletion_notification(log_entry, user="system"):
        """
        Send notification when a credit check log is deleted.

        Args:
            log_entry: CreditCheckLog instance
            user: User performing the action
        """
        try:
            NotificationService.create(
                data={
                    'title': 'Credit Check Log Deleted',
                    'message': (
                        f'Credit check log #{log_entry.id} for debtor '
                        f'#{log_entry.debtor_id} has been deleted by {user}.'
                    ),
                    'type': 'info',
                    'metadata': {
                        'log_id': log_entry.id,
                        'debtor_id': log_entry.debtor_id,
                        'score': log_entry.score,
                        'risk_level': log_entry.risk_level,
                        'deleted_by': user,
                    },
                },
                user=user,
                request=None
            )

            logger.info(
                f"[CreditCheckTransition] Deletion notification sent for "
                f"log #{log_entry.id}"
            )

        except Exception as e:
            logger.error(
                f"[CreditCheckTransition] Failed to send deletion notification: {e}"
            )

    @staticmethod
    def _update_borrower_credit_rating(log_entry, user="system"):
        """
        Update borrower's credit rating based on the latest credit check.

        This method updates the Borrower model if it has a `credit_rating` field.
        To use this, you need to add a `credit_rating` field to the Borrower model.

        Args:
            log_entry: CreditCheckLog instance
            user: User performing the action
        """
        try:
            # Check if Borrower model has credit_rating field
            if not hasattr(Borrower, 'credit_rating'):
                logger.debug(
                    f"[CreditCheckTransition] Borrower model does not have "
                    f"credit_rating field - skipping update"
                )
                return

            # Determine rating based on score
            if log_entry.score >= 750:
                rating = CreditCheckStateTransitionService.RATING_EXCELLENT
            elif log_entry.score >= 700:
                rating = CreditCheckStateTransitionService.RATING_GOOD
            elif log_entry.score >= 500:
                rating = CreditCheckStateTransitionService.RATING_FAIR
            else:
                rating = CreditCheckStateTransitionService.RATING_POOR

            # Update borrower
            updated_count = Borrower.objects.filter(
                id=log_entry.debtor_id,
                deleted_at__isnull=True
            ).update(
                credit_rating=rating,
                updated_at=timezone.now()
            )

            if updated_count > 0:
                logger.info(
                    f"[CreditCheckTransition] Borrower #{log_entry.debtor_id} "
                    f"credit rating updated to: {rating}"
                )

        except Exception as e:
            logger.error(
                f"[CreditCheckTransition] Failed to update credit rating: {e}"
            )

    @staticmethod
    def _recalculate_borrower_rating(log_entry, user="system"):
        """
        Recalculate borrower's credit rating based on remaining logs.

        This recomputes the average score from all non-deleted credit checks
        for the borrower and updates their rating.

        Args:
            log_entry: CreditCheckLog instance
            user: User performing the action
        """
        try:
            # Check if Borrower model has credit_rating field
            if not hasattr(Borrower, 'credit_rating'):
                return

            # Get all remaining credit checks for this borrower
            remaining_logs = CreditCheckLog.objects.filter(
                debtor_id=log_entry.debtor_id,
                deleted_at__isnull=True
            )

            if remaining_logs.exists():
                # Calculate average score
                avg_score = remaining_logs.aggregate(
                    avg=models.Avg('score')
                )['avg'] or 0

                # Determine rating
                if avg_score >= 750:
                    rating = CreditCheckStateTransitionService.RATING_EXCELLENT
                elif avg_score >= 700:
                    rating = CreditCheckStateTransitionService.RATING_GOOD
                elif avg_score >= 500:
                    rating = CreditCheckStateTransitionService.RATING_FAIR
                else:
                    rating = CreditCheckStateTransitionService.RATING_POOR

                # Update borrower
                Borrower.objects.filter(
                    id=log_entry.debtor_id,
                    deleted_at__isnull=True
                ).update(
                    credit_rating=rating,
                    updated_at=timezone.now()
                )

                logger.info(
                    f"[CreditCheckTransition] Borrower #{log_entry.debtor_id} "
                    f"credit rating recalculated to: {rating} "
                    f"(avg score: {avg_score:.2f}, remaining logs: {remaining_logs.count()})"
                )

        except Exception as e:
            logger.error(
                f"[CreditCheckTransition] Failed to recalculate credit rating: {e}"
            )

    # ============================================================
    # UTILITY METHODS
    # ============================================================

    @staticmethod
    def get_credit_rating_from_score(score):
        """
        Get credit rating string from a score.

        Args:
            score: Credit score (300-850)

        Returns:
            str: Credit rating ('Excellent', 'Good', 'Fair', 'Poor')
        """
        if score >= 750:
            return CreditCheckStateTransitionService.RATING_EXCELLENT
        elif score >= 700:
            return CreditCheckStateTransitionService.RATING_GOOD
        elif score >= 500:
            return CreditCheckStateTransitionService.RATING_FAIR
        else:
            return CreditCheckStateTransitionService.RATING_POOR

    @staticmethod
    def get_risk_level_from_score(score):
        """
        Get risk level from a score.

        Args:
            score: Credit score (300-850)

        Returns:
            str: Risk level ('Low', 'Medium', 'High')
        """
        if score >= 700:
            return CreditCheckLog.RiskLevel.LOW
        elif score >= 500:
            return CreditCheckLog.RiskLevel.MEDIUM
        else:
            return CreditCheckLog.RiskLevel.HIGH

    @staticmethod
    def is_score_passing(score, threshold=600):
        """
        Check if a credit score passes the minimum threshold.

        Args:
            score: Credit score (300-850)
            threshold: Minimum passing score (default: 600)

        Returns:
            bool: True if score passes
        """
        return score >= threshold

    @staticmethod
    def is_score_excellent(score, threshold=750):
        """
        Check if a credit score is excellent.

        Args:
            score: Credit score (300-850)
            threshold: Excellent score threshold (default: 750)

        Returns:
            bool: True if score is excellent
        """
        return score >= threshold

    @staticmethod
    def is_score_poor(score, threshold=500):
        """
        Check if a credit score is poor.

        Args:
            score: Credit score (300-850)
            threshold: Poor score threshold (default: 500)

        Returns:
            bool: True if score is poor
        """
        return score < threshold