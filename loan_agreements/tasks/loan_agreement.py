# loan_agreements/tasks/loan_agreement.py
import logging
from datetime import timedelta
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from celery import shared_task

from loan_agreements.models.loan_agreement import LoanAgreement
from loan_agreements.services.loan_agreement import LoanAgreementService
from debts.models.debt import Debt
from notifications.services.notification import NotificationService
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


# ============================================================
# CLEANUP OLD DRAFT AGREEMENTS
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_old_draft_agreements(self, days: int = 30, user: str = 'system'):
    """
    Soft-delete draft agreements that have not been signed for N days.
    
    Args:
        days: Number of days after which a draft agreement is considered abandoned
        user: User performing the action (for audit)
    
    Returns:
        dict: {'deleted_count': int, 'message': str}
    """
    logger.info(f"[LOAN AGREEMENT TASK] Cleaning up draft agreements older than {days} days...")

    try:
        cutoff = timezone.now() - timedelta(days=days)
        
        # Find draft agreements older than cutoff
        draft_agreements = LoanAgreement.objects.filter(
            status=LoanAgreement.Status.DRAFT,
            created_at__lt=cutoff,
            deleted_at__isnull=True
        ).select_related('debt', 'debt__borrower')
        
        count = draft_agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No old draft agreements found.")
            return {'deleted_count': 0, 'message': 'No old draft agreements found.'}

        deleted_list = []
        for agreement in draft_agreements:
            try:
                # Soft delete the agreement
                LoanAgreementService.delete(
                    agreement_id=agreement.id,
                    user=user,
                    request=None,
                    allow_delete_signed=False  # Not signed anyway
                )
                deleted_list.append(agreement.id)
                logger.info(f"[LOAN AGREEMENT TASK] Deleted draft agreement #{agreement.id}")
            except Exception as e:
                logger.error(f"[LOAN AGREEMENT TASK] Failed to delete agreement #{agreement.id}: {e}")

        # Notify admins
        if deleted_list:
            NotificationService.notify_admins_and_staff(
                title='📄 Draft Agreement Cleanup Completed',
                message=f'Deleted {len(deleted_list)} draft agreements older than {days} days.',
                type='info',
                metadata={
                    'deleted_count': len(deleted_list),
                    'days': days,
                    'deleted_ids': deleted_list[:10],  # First 10 only
                },
                user=user
            )

        return {
            'deleted_count': len(deleted_list),
            'message': f'Deleted {len(deleted_list)} draft agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Cleanup failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# NOTIFY OVERDUE AGREEMENTS (SIGNED AGREEMENTS WITH PAST DUE DATE)
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def notify_overdue_agreements(self, user: str = 'system'):
    """
    Find signed agreements where the debt due date has passed, and notify admins/staff.
    """
    logger.info("[LOAN AGREEMENT TASK] Checking for overdue signed agreements...")

    try:
        today = timezone.now().date()
        
        # Find signed agreements linked to debts with due_date < today
        agreements = LoanAgreement.objects.filter(
            status=LoanAgreement.Status.SIGNED,
            debt__due_date__lt=today,
            debt__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE],
            deleted_at__isnull=True
        ).select_related('debt', 'debt__borrower')
        
        count = agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No overdue signed agreements.")
            return {'overdue_count': 0, 'message': 'No overdue signed agreements found.'}

        # Log and notify
        details = []
        for agreement in agreements:
            details.append({
                'agreement_id': agreement.id,
                'debt_id': agreement.debt_id,
                'borrower_name': agreement.debt.borrower.name if agreement.debt.borrower else 'Unknown',
                'due_date': agreement.debt.due_date.isoformat(),
                'days_overdue': (today - agreement.debt.due_date).days,
            })
            logger.info(f"[LOAN AGREEMENT TASK] Overdue agreement #{agreement.id} for debt #{agreement.debt_id}")

        NotificationService.notify_admins_and_staff(
            title='⚠️ Overdue Signed Agreements Detected',
            message=f'Found {count} signed agreements with overdue debts.',
            type='warning',
            metadata={
                'overdue_count': count,
                'details': details[:10],
            },
            user=user
        )

        return {
            'overdue_count': count,
            'details': details[:20],
            'message': f'Found {count} overdue signed agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Notify overdue failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# AUTO-ASSIGN AGREEMENT TO DEBT (if missing)
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_assign_agreements(self, user: str = 'system'):
    """
    Auto-create draft agreements for debts that have no agreement yet.
    This can be used as a nightly job to ensure all debts have an agreement.
    """
    logger.info("[LOAN AGREEMENT TASK] Auto-assigning agreements to debts without one...")

    try:
        # Find debts that have no agreements (active/overdue status)
        debts_without_agreement = Debt.objects.filter(
            deleted_at__isnull=True,
            status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
        ).exclude(
            id__in=LoanAgreement.objects.filter(
                deleted_at__isnull=True
            ).values_list('debt_id', flat=True).distinct()
        )

        count = debts_without_agreement.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] All debts have agreements.")
            return {'created_count': 0, 'message': 'All debts have agreements.'}

        created = []
        errors = []

        for debt in debts_without_agreement[:50]:  # Limit per run to avoid huge load
            try:
                # Create a draft agreement
                data = {
                    'debt_id': debt.id,
                    'status': LoanAgreement.Status.DRAFT,
                    'lender_name': 'Collectly',  # default lender
                    'agreement_date': timezone.now().date(),
                    'principal_amount': debt.total_amount,
                    'interest_rate': debt.interest_rate,
                    'penalty_rate': debt.penalty_rate,
                    'due_date': debt.due_date,
                }
                agreement = LoanAgreementService.create(data, user=user, request=None)
                created.append(agreement.id)
                logger.info(f"[LOAN AGREEMENT TASK] Created draft agreement #{agreement.id} for debt #{debt.id}")
            except Exception as e:
                errors.append({'debt_id': debt.id, 'error': str(e)})
                logger.error(f"[LOAN AGREEMENT TASK] Failed to create agreement for debt #{debt.id}: {e}")

        # Notify admins
        if created:
            NotificationService.notify_admins_and_staff(
                title='📄 Auto-Assigned Agreements Created',
                message=f'Created {len(created)} draft agreements for debts without one.',
                type='info',
                metadata={
                    'created_count': len(created),
                    'errors_count': len(errors),
                    'created_ids': created[:10],
                },
                user=user
            )

        return {
            'created_count': len(created),
            'errors': errors,
            'message': f'Created {len(created)} agreements.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Auto-assign failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# BULK IMPORT AS TASK
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def process_loan_agreement_bulk_import(self, file_path: str, user: str = 'system', request_data=None):
    """
    Process bulk import of loan agreements from CSV file.
    """
    logger.info(f"[LOAN AGREEMENT TASK] Starting bulk import from {file_path}")

    try:
        result = LoanAgreementService.import_from_csv(
            file_path=file_path,
            user=user,
            request=request_data
        )

        imported_count = len(result.get('imported', []))
        errors_count = len(result.get('errors', []))

        # Notify admins/staff
        NotificationService.notify_admins_and_staff(
            title='📥 Loan Agreement Import Completed',
            message=f'Import completed: {imported_count} imported, {errors_count} failed.',
            type='info' if errors_count == 0 else 'error',
            metadata=result,
            user=user
        )

        logger.info(f"[LOAN AGREEMENT TASK] Bulk import completed: {imported_count} imported")
        return result

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Bulk import failed: {e}")
        raise self.retry(exc=e, countdown=120)


# ============================================================
# FORCE SYNC AGREEMENT STATUS WITH DEBT
# ============================================================

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def sync_agreement_statuses(self, user: str = 'system'):
    """
    Sync agreement statuses with debt statuses. For example, if a debt is paid,
    mark all its agreements as "completed" or "archived" (if we had such status).
    Currently, no completed/archived status exists, but we can log or update.
    """
    logger.info("[LOAN AGREEMENT TASK] Syncing agreement statuses with debts...")

    try:
        # Find signed agreements linked to debts that are now fully paid
        paid_debts = Debt.objects.filter(
            status=Debt.Status.PAID,
            deleted_at__isnull=True
        ).values_list('id', flat=True)

        agreements = LoanAgreement.objects.filter(
            debt_id__in=paid_debts,
            status=LoanAgreement.Status.SIGNED,
            deleted_at__isnull=True
        )

        count = agreements.count()
        if count == 0:
            logger.info("[LOAN AGREEMENT TASK] No agreements to sync.")
            return {'updated_count': 0, 'message': 'No agreements to sync.'}

        # For now, we can just log or update a custom field if we add one.
        # Since no status update, we'll just notify.
        NotificationService.notify_admins_and_staff(
            title='📄 Agreement Status Sync Completed',
            message=f'Found {count} agreements linked to paid debts. They might need archiving.',
            type='info',
            metadata={
                'agreement_count': count,
                'paid_debt_ids': list(paid_debts)[:10],
            },
            user=user
        )

        return {
            'updated_count': 0,  # No status changes, just notification
            'agreements_linked_to_paid_debts': count,
            'message': f'Found {count} agreements linked to paid debts.'
        }

    except Exception as e:
        logger.error(f"[LOAN AGREEMENT TASK] Sync failed: {e}")
        raise self.retry(exc=e, countdown=120)