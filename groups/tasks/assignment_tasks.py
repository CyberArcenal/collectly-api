# groups/tasks/assignment_tasks.py
import logging
from typing import List, Dict, Any

from celery import shared_task

from groups.models.debtor_group_member import DebtorGroupMember
from groups.services.group import GroupService
from notifications.services.notification import NotificationService
from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from django.db import models
from django.db.models import Q, Count, OuterRef, Subquery, Sum

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def bulk_assign_borrowers_to_group(
    self,
    group_id: int,
    debtor_ids: List[int],
    user: str = 'system',
    batch_size: int = 100,
):
    """
    Bulk assign multiple borrowers to a group asynchronously.
    """
    logger.info(f"[GROUP TASK] Starting bulk assign: group_id={group_id}, total={len(debtor_ids)}")

    try:
        group = GroupService.get_group_by_id(group_id)
        if not group:
            return {
                'assigned_count': 0,
                'errors': [{'error': f'Group {group_id} not found'}]
            }

        total_assigned = 0
        all_errors = []

        for i in range(0, len(debtor_ids), batch_size):
            batch = debtor_ids[i:i + batch_size]
            logger.info(f"[GROUP TASK] Processing batch {i//batch_size + 1}: {len(batch)} borrowers")

            try:
                result = GroupService.bulk_assign(
                    group_id=group_id,
                    debtor_ids=batch,
                    user=user,
                    request=None
                )
                total_assigned += result.get('assigned_count', 0)
                all_errors.extend(result.get('errors', []))
            except Exception as e:
                logger.error(f"[GROUP TASK] Batch failed: {e}")
                all_errors.append({'batch': i//batch_size + 1, 'error': str(e)})

        if total_assigned > 0 or all_errors:
            NotificationService.notify_admins_and_staff(
                title='📋 Bulk Assign Completed',
                message=f'Assigned {total_assigned} borrowers to group "{group.name}", {len(all_errors)} errors.',
                type='info' if not all_errors else 'error',
                metadata={
                    'group_id': group_id,
                    'group_name': group.name,
                    'assigned': total_assigned,
                    'errors': all_errors[:10]
                },
                user=user
            )

        logger.info(f"[GROUP TASK] Bulk assign completed: {total_assigned} assigned, {len(all_errors)} errors")
        return {
            'assigned_count': total_assigned,
            'errors': all_errors
        }

    except Exception as e:
        logger.error(f"[GROUP TASK] Bulk assign failed: {e}")
        raise self.retry(exc=e, countdown=120)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def auto_assign_borrowers_to_groups(
    self,
    group_id: int,
    criteria: Dict[str, Any],
    user: str = 'system',
    batch_size: int = 100
):
    """
    Auto-assign borrowers to a group based on criteria.
    """
    logger.info(f"[GROUP TASK] Auto-assign starting: group_id={group_id}, criteria={criteria}")

    try:
        group = GroupService.get_group_by_id(group_id)
        if not group:
            return {
                'assigned_count': 0,
                'total_candidates': 0,
                'errors': [{'error': f'Group {group_id} not found'}]
            }

        qs = Borrower.objects.filter(deleted_at__isnull=True)

        # Apply criteria
        if criteria.get('credit_rating'):
            qs = qs.filter(credit_rating=criteria['credit_rating'])

        if criteria.get('credit_rating__in'):
            qs = qs.filter(credit_rating__in=criteria['credit_rating__in'])

        if criteria.get('has_email'):
            qs = qs.filter(email__isnull=False).exclude(email='')

        if criteria.get('has_contact'):
            qs = qs.filter(contact__isnull=False).exclude(contact='')

        if criteria.get('total_debt__gte'):
            total_debt_subquery = Debt.objects.filter(
                borrower=OuterRef('id'),
                deleted_at__isnull=True,
                status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
            ).values('borrower_id').annotate(total=Sum('remaining_amount')).values('total')
            qs = qs.annotate(total_debt=Subquery(total_debt_subquery, output_field=models.DecimalField()))
            qs = qs.filter(total_debt__gte=criteria['total_debt__gte'])

        if criteria.get('active_debt_count__gte'):
            qs = qs.annotate(active_debt_count=Count('debts', filter=Q(
                debts__deleted_at__isnull=True,
                debts__status__in=[Debt.Status.ACTIVE, Debt.Status.OVERDUE]
            )))
            qs = qs.filter(active_debt_count__gte=criteria['active_debt_count__gte'])

        # Exclude already in group
        already_in_group = DebtorGroupMember.objects.filter(
            group_id=group_id,
            deleted_at__isnull=True
        ).values_list('debtor_id', flat=True)
        qs = qs.exclude(id__in=already_in_group)

        total_candidates = qs.count()
        logger.info(f"[GROUP TASK] Found {total_candidates} candidates for auto-assign")

        if total_candidates == 0:
            return {
                'assigned_count': 0,
                'total_candidates': 0,
                'errors': [],
                'message': 'No candidates found matching criteria'
            }

        borrower_ids = list(qs.values_list('id', flat=True))
        result = bulk_assign_borrowers_to_group(
            group_id=group_id,
            debtor_ids=borrower_ids,
            user=user,
            batch_size=batch_size
        )
        result['total_candidates'] = total_candidates
        return result

    except Exception as e:
        logger.error(f"[GROUP TASK] Auto-assign failed: {e}")
        raise self.retry(exc=e, countdown=300)