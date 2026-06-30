import logging
from django.db import transaction
from django.db.models import Q, Count
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.models.notification import Notification
from debts.models.debt import Debt
from users.services.notification_recipients import get_admin_and_staff_users
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    def notify_admins_and_staff(title, message, type="info", metadata=None, user="system"):
        """
        Send notification to all admin and staff users.

        Args:
            title: Notification title
            message: Notification message
            type: Notification type
            metadata: Additional metadata
            user: User performing the action

        Returns:
            int: Number of notifications created
        """
        recipients = get_admin_and_staff_users()
        count = 0

        for recipient in recipients:
            try:
                NotificationService.create(
                    data={
                        'title': title,
                        'message': message,
                        'type': type,
                        'metadata': metadata or {},
                    },
                    user=user,
                    request=None
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to send notification to user {recipient.id}: {e}")

        logger.info(f"Sent notification to {count} admin/staff users: {title}")
        return count

    @staticmethod
    def notify_all_admins(title, message, type="info", metadata=None, user="system"):
        """Send notification to all admin users only."""
        from users.services.notification_recipients import get_admin_users
        recipients = get_admin_users()
        count = 0

        for recipient in recipients:
            try:
                NotificationService.create(
                    data={
                        'title': title,
                        'message': message,
                        'type': type,
                        'metadata': metadata or {},
                    },
                    user=user,
                    request=None
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to send notification to admin {recipient.id}: {e}")

        return count

    @staticmethod
    def notify_admins_staff_managers(title, message, type="info", metadata=None, user="system"):
        """Send notification to admin, staff, and manager users."""
        from users.services.notification_recipients import get_admin_staff_manager_users
        recipients = get_admin_staff_manager_users()
        count = 0

        for recipient in recipients:
            try:
                NotificationService.create(
                    data={
                        'title': title,
                        'message': message,
                        'type': type,
                        'metadata': metadata or {},
                    },
                    user=user,
                    request=None
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to send notification to user {recipient.id}: {e}")

        return count
    
    """
    Service layer for Notification CRUD operations.
    """

    @staticmethod
    def get_by_id(notification_id, include_deleted=False):
        """
        Get a single notification by ID.
        """
        qs = Notification.objects.select_related('debt')
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        try:
            return qs.get(id=notification_id)
        except Notification.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='created_at', sort_order='desc'):
        """
        Get paginated list of notifications with filters.
        """
        qs = Notification.objects.filter(deleted_at__isnull=True)
        
        if filters:
            if filters.get('debt_id'):
                qs = qs.filter(debt_id=filters['debt_id'])
            if filters.get('type'):
                qs = qs.filter(type=filters['type'])
            if filters.get('is_read') is not None:
                qs = qs.filter(is_read=filters['is_read'])
            if filters.get('scheduled_from'):
                qs = qs.filter(scheduled_for__gte=filters['scheduled_from'])
            if filters.get('scheduled_to'):
                qs = qs.filter(scheduled_for__lte=filters['scheduled_to'])
            if filters.get('from_date'):
                qs = qs.filter(created_at__gte=filters['from_date'])
            if filters.get('to_date'):
                qs = qs.filter(created_at__lte=filters['to_date'])
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(title__icontains=search) |
                    Q(message__icontains=search)
                )
            if filters.get('include_deleted'):
                qs = Notification.objects.all()
        
        # Apply sorting
        if sort_order.lower() == 'asc':
            sort_by = sort_by
        else:
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)
        
        return paginate_queryset(qs, page, limit)

    @staticmethod
    @transaction.atomic
    def create(data, user=None, request=None):
        """
        Create a new notification.
        """
        debt = None
        if data.get('debt_id'):
            debt = Debt.objects.filter(id=data['debt_id']).first()
            if not debt:
                raise ValidationError({'debt_id': 'Debt not found.'})
        
        notification = Notification.objects.create(
            debt=debt,
            title=data['title'],
            message=data['message'],
            type=data.get('type', Notification.Type.REMINDER),
            is_read=data.get('is_read', False),
            scheduled_for=data.get('scheduled_for')
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_create',
                model_name='Notification',
                object_id=str(notification.id),
                changes={'data': data}
            )
        
        logger.info(f"Notification created: {notification.id}")
        return notification

    @staticmethod
    @transaction.atomic
    def update(notification_id, data, user=None, request=None):
        """
        Update a notification.
        """
        notification = NotificationService.get_by_id(notification_id)
        if not notification:
            raise ValidationError({'id': 'Notification not found.'})
        
        # Update debt if changed
        if 'debt_id' in data:
            if data['debt_id'] is None:
                notification.debt = None
            else:
                debt = Debt.objects.filter(id=data['debt_id']).first()
                if not debt:
                    raise ValidationError({'debt_id': 'Debt not found.'})
                notification.debt = debt
        
        # Update fields
        update_fields = ['title', 'message', 'type', 'is_read', 'scheduled_for']
        for field in update_fields:
            if field in data:
                setattr(notification, field, data[field])
        
        notification.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_update',
                model_name='Notification',
                object_id=str(notification.id),
                changes={'data': data}
            )
        
        logger.info(f"Notification updated: {notification.id}")
        return notification

    @staticmethod
    @transaction.atomic
    def delete(notification_id, user=None, request=None):
        """
        Soft delete a notification.
        """
        notification = NotificationService.get_by_id(notification_id)
        if not notification:
            raise ValidationError({'id': 'Notification not found.'})
        
        notification.soft_delete()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_delete',
                model_name='Notification',
                object_id=str(notification.id),
                changes={'deleted_at': notification.deleted_at}
            )
        
        logger.info(f"Notification soft-deleted: {notification.id}")
        return notification

    @staticmethod
    @transaction.atomic
    def mark_as_read(notification_id, user=None, request=None):
        """
        Mark a notification as read.
        """
        notification = NotificationService.get_by_id(notification_id)
        if not notification:
            raise ValidationError({'id': 'Notification not found.'})
        
        notification.mark_as_read()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_read',
                model_name='Notification',
                object_id=str(notification.id),
                changes={'is_read': True}
            )
        
        return notification

    @staticmethod
    @transaction.atomic
    def mark_all_as_read(user=None, request=None):
        """
        Mark all notifications as read for a user.
        """
        # Since we don't have user-specific notifications yet,
        # we mark all as read
        count = Notification.objects.filter(
            is_read=False,
            deleted_at__isnull=True
        ).update(is_read=True)
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notifications_read_all',
                model_name='Notification',
                object_id='all',
                changes={'count': count}
            )
        
        logger.info(f"Marked {count} notifications as read")
        return {'count': count}

    @staticmethod
    def get_unread_count():
        """
        Get count of unread notifications.
        """
        return Notification.objects.filter(
            is_read=False,
            deleted_at__isnull=True
        ).count()

    @staticmethod
    def get_statistics():
        """
        Get notification statistics.
        """
        qs = Notification.objects.filter(deleted_at__isnull=True)
        
        total = qs.count()
        unread = qs.filter(is_read=False).count()
        read = qs.filter(is_read=True).count()
        
        # By type
        by_type = qs.values('type').annotate(count=Count('id')).order_by('-count')
        
        # Scheduled for future
        now = timezone.now()
        scheduled = qs.filter(
            scheduled_for__isnull=False,
            scheduled_for__gt=now
        ).count()
        
        # Last 7 days
        seven_days_ago = now - timezone.timedelta(days=7)
        recent = qs.filter(created_at__gte=seven_days_ago).count()
        
        return {
            'total': total,
            'unread': unread,
            'read': read,
            'by_type': list(by_type),
            'scheduled_future': scheduled,
            'created_last_7_days': recent,
        }