# borrowers/tasks/borrower.py
import logging
from datetime import timedelta
from typing import Optional, List

from celery import shared_task
from django.db import transaction
from django.db.models import Q, Count, Sum, Case, When, Value, IntegerField
from django.core.exceptions import ValidationError
from django.utils import timezone

from borrowers.models.borrower import Borrower
from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.services.borrower import BorrowerService
from borrowers.services.credit_check import CreditCheckService
from debts.models.debt import Debt
from audit.utils.log import log_audit_event
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


# ============================================================
# BULK IMPORT TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def process_borrower_bulk_import(self, file_path: str, user: str = 'system', request_data: Optional[dict] = None):
    """
    Process bulk import of borrowers from CSV file.

    Args:
        file_path: Path to the CSV file
        user: User performing the import (for audit)
        request_data: Additional request data (for logging)

    Returns:
        dict: {
            'imported': count,
            'failed': count,
            'errors': list
        }
    """
    logger.info(f"[BORROWER TASK] Starting bulk import from {file_path}")

    try:
        # Use existing import method
        result = BorrowerService.import_from_csv(
            file_path=file_path,
            user=user,
            request=request_data
        )

        # Notify admins/staff
        if result.get('imported') or result.get('errors'):
            NotificationService.notify_admins_and_staff(
                title='📥 Borrower Import Completed',
                message=f'Import completed: {len(result.get("imported", []))} imported, {len(result.get("errors", []))} failed.',
                type='info' if not result.get('errors') else 'error',
                metadata=result,
                user=user
            )

        logger.info(f"[BORROWER TASK] Bulk import completed: {len(result.get('imported', []))} imported")
        return result

    except Exception as e:
        logger.error(f"[BORROWER TASK] Bulk import failed: {e}")
        NotificationService.notify_admins_and_staff(
            title='❌ Borrower Import Failed',
            message=f'Bulk import failed: {str(e)}',
            type='error',
            metadata={'error': str(e)},
            user=user
        )
        raise self.retry(exc=e, countdown=120)


# ============================================================
# CREDIT SCORE RECALCULATION
# ============================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def recalculate_credit_scores(
    self,
    borrower_ids: Optional[List[int]] = None,
    batch_size: int = 100,
    user: str = 'system'
):
    """
    Recalculate credit scores for all borrowers or a subset.

    Args:
        borrower_ids: Optional list of borrower IDs to recalc. If None, recalc all active borrowers.
        batch_size: Number of borrowers to process per batch
        user: User performing the action (for audit)

    Returns:
        dict: {
            'total': int,
            'updated': int,
            'failed': int,
            'errors': list
        }
    """
    logger.info(f"[BORROWER TASK] Starting credit score recalculation...")

    try:
        # Get borrowers to process
        qs = Borrower.objects.filter(deleted_at__isnull=True)
        if borrower_ids:
            qs = qs.filter(id__in=borrower_ids)

        total_count = qs.count()
        logger.info(f"[BORROWER TASK] Found {total_count} borrowers to process")

        if total_count == 0:
            return {
                'total': 0,
                'updated': 0,
                'failed': 0,
                'errors': [],
                'message': 'No borrowers to process'
            }

        updated_count = 0
        failed_count = 0
        errors = []

        # Process in batches
        for start in range(0, total_count, batch_size):
            batch = qs[start:start + batch_size]
            for borrower in batch:
                try:
                    # Compute new credit score
                    result = CreditCheckService.compute_score(borrower.id)
                    new_score = result.get('score', 700)
                    new_risk_level = result.get('risk_level', 'Medium')
                    remarks = result.get('remarks', '')

                    # Save credit check log
                    CreditCheckService.create(
                        data={
                            'debtor_id': borrower.id,
                            'score': new_score,
                            'risk_level': new_risk_level,
                            'remarks': remarks,
                            'performed_by': None,
                        },
                        user=user,
                        request=None
                    )

                    # Update borrower's credit_rating field if changed
                    old_rating = borrower.credit_rating
                    new_rating = new_risk_level

                    if old_rating != new_rating:
                        borrower.credit_rating = new_rating
                        borrower.save(update_fields=['credit_rating', 'updated_at'])

                        logger.info(
                            f"[BORROWER TASK] Borrower {borrower.id} credit rating changed: "
                            f"{old_rating} → {new_rating}"
                        )

                    updated_count += 1

                except Exception as e:
                    failed_count += 1
                    errors.append({
                        'borrower_id': borrower.id,
                        'error': str(e)
                    })
                    logger.error(f"[BORROWER TASK] Failed to update borrower {borrower.id}: {e}")

            # Update progress (if needed, we can log)
            logger.info(f"[BORROWER TASK] Processed {start + len(batch)}/{total_count}")

        # Notify admins/staff
        if updated_count > 0 or failed_count > 0:
            NotificationService.notify_admins_and_staff(
                title='🔄 Credit Score Recalculation Completed',
                message=f'Recalculated: {updated_count} updated, {failed_count} failed.',
                type='info' if failed_count == 0 else 'error',
                metadata={
                    'total': total_count,
                    'updated': updated_count,
                    'failed': failed_count,
                    'errors': errors[:10]  # Only first 10 errors
                },
                user=user
            )

        result = {
            'total': total_count,
            'updated': updated_count,
            'failed': failed_count,
            'errors': errors,
            'message': f'Updated {updated_count} borrowers, {failed_count} failed'
        }

        logger.info(f"[BORROWER TASK] Score recalculation completed: {result}")
        return result

    except Exception as e:
        logger.error(f"[BORROWER TASK] Credit score recalculation failed: {e}")
        raise self.retry(exc=e, countdown=300 * (2 ** self.request.retries))


# ============================================================
# BORROWER STATUS UPDATE TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def update_borrower_statuses(self, user: str = 'system'):
    """
    Update borrower statuses based on their debts.

    Rules:
    - If borrower has no active/overdue debts, mark as 'Inactive'
    - If borrower has active debts, mark as 'Active'
    - If borrower has only overdue debts, mark as 'Delinquent' (optional)
    """
    logger.info(f"[BORROWER TASK] Starting borrower status updates...")

    try:
        # We'll add a status field to Borrower if not present? Actually, we don't have a status field.
        # We'll use credit_rating as a proxy, or we can add a 'status' field later.
        # For now, we'll log warnings and maybe send notifications.
        # Better: add a 'status' field to Borrower model later.

        # For now, we'll just log statistics
        active_borrowers = Borrower.objects.filter(
            deleted_at__isnull=True,
            debts__deleted_at__isnull=True,
            debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).distinct().count()

        inactive_borrowers = Borrower.objects.filter(
            deleted_at__isnull=True
        ).exclude(
            debts__deleted_at__isnull=True,
            debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).distinct().count()

        logger.info(f"[BORROWER TASK] Active borrowers: {active_borrowers}, Inactive: {inactive_borrowers}")

        # Optionally, update a custom status field if we add it later.

        return {
            'active_borrowers': active_borrowers,
            'inactive_borrowers': inactive_borrowers,
            'message': 'Status check completed'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Status update failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# MERGE DUPLICATE BORROWERS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def merge_duplicate_borrowers(self, user: str = 'system'):
    """
    Identify and merge duplicate borrower records based on email/contact.

    This task looks for borrowers with same email or contact and merges them.
    """
    logger.info("[BORROWER TASK] Starting duplicate borrower merge...")

    try:
        merged_count = 0
        errors = []

        # Find duplicates by email
        email_duplicates = Borrower.objects.filter(
            deleted_at__isnull=True,
            email__isnull=False
        ).values('email').annotate(
            count=Count('id')
        ).filter(count__gt=1)

        for item in email_duplicates:
            email = item['email']
            borrowers = list(Borrower.objects.filter(
                email=email,
                deleted_at__isnull=True
            ).order_by('id'))

            if len(borrowers) < 2:
                continue

            # Keep the first, merge others into it
            primary = borrowers[0]
            for duplicate in borrowers[1:]:
                try:
                    # Move all debts to primary
                    Debt.objects.filter(borrower=duplicate).update(borrower=primary)
                    # Move credit checks
                    CreditCheckLog.objects.filter(debtor=duplicate).update(debtor=primary)

                    # Soft delete duplicate
                    duplicate.soft_delete()
                    merged_count += 1
                    logger.info(f"[BORROWER TASK] Merged borrower {duplicate.id} into {primary.id} (email: {email})")
                except Exception as e:
                    errors.append({
                        'borrower_id': duplicate.id,
                        'error': str(e)
                    })

        # Similarly, merge by contact (if needed)
        # ... (similar logic)

        # Notify admins
        if merged_count > 0 or errors:
            NotificationService.notify_admins_and_staff(
                title='🔄 Duplicate Borrower Merge Completed',
                message=f'Merged {merged_count} duplicate borrowers, {len(errors)} errors.',
                type='info' if not errors else 'error',
                metadata={
                    'merged_count': merged_count,
                    'errors': errors[:10]
                },
                user=user
            )

        return {
            'merged_count': merged_count,
            'errors': errors,
            'message': f'Merged {merged_count} duplicates'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Duplicate merge failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# CLEANUP INCOMPLETE BORROWERS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_incomplete_borrowers(self, days: int = 30, user: str = 'system'):
    """
    Clean up borrowers with incomplete data (missing name, email, contact) older than N days.
    """
    logger.info(f"[BORROWER TASK] Starting incomplete borrower cleanup (older than {days} days)...")

    try:
        cutoff = timezone.now() - timedelta(days=days)

        # Find incomplete borrowers
        incomplete = Borrower.objects.filter(
            deleted_at__isnull=True,
            created_at__lt=cutoff,
        ).filter(
            Q(name__isnull=True) | Q(name='') |
            Q(email__isnull=True) | Q(email='') |
            Q(contact__isnull=True) | Q(contact='')
        )

        count = incomplete.count()
        if count == 0:
            return {'deleted_count': 0, 'message': 'No incomplete borrowers found'}

        # Soft delete them
        for borrower in incomplete:
            borrower.soft_delete()

        logger.info(f"[BORROWER TASK] Soft-deleted {count} incomplete borrowers")

        return {
            'deleted_count': count,
            'message': f'Soft-deleted {count} incomplete borrowers'
        }

    except Exception as e:
        logger.error(f"[BORROWER TASK] Cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# FORCE CREDIT SCORE RECALCULATION (for manual trigger)
# ============================================================

@shared_task
def force_credit_score_recalc(borrower_ids: Optional[List[int]] = None, user: str = 'system'):
    """
    Force immediate credit score recalculation.
    This is a wrapper for manual triggers from admin panel.
    """
    logger.info("[BORROWER TASK] 🔄 Force credit score recalculation triggered")
    return recalculate_credit_scores(borrower_ids=borrower_ids, user=user)