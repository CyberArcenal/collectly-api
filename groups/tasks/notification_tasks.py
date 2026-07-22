# groups/tasks/notification_tasks.py
import logging
from django.utils import timezone
from celery import shared_task

from groups.services.group import GroupService
from notifications.services.notification import NotificationService

logger = logging.getLogger(__name__)


@shared_task
def notify_group_change(group_id: int, action: str, user: str = 'system'):
    """
    Send notification when a group is created, updated, or deleted.
    """
    try:
        group = GroupService.get_group_by_id(group_id)
        if not group and action != 'deleted':
            return

        group_name = group.name if group else f'Group #{group_id}'

        NotificationService.notify_admins_and_staff(
            title=f'📋 Group {action.capitalize()}',
            message=f'Group "{group_name}" has been {action}.',
            type='info',
            metadata={
                'group_id': group_id,
                'action': action,
                'user': user,
                'timestamp': timezone.now().isoformat()
            },
            user=user
        )
    except Exception as e:
        logger.error(f"[GROUP TASK] Notification failed: {e}")