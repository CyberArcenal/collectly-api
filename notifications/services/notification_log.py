import logging
from django.db import transaction
from django.db.models import Q, Avg, Count
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.models.notification_log import NotificationLog
from utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


class NotificationLogService:
    """
    Service layer for NotificationLog operations.
    """

    @staticmethod
    def get_by_id(log_id):
        """
        Get a single notification log by ID.
        """
        try:
            return NotificationLog.objects.get(id=log_id)
        except NotificationLog.DoesNotExist:
            return None

    @staticmethod
    def get_list(filters=None, page=1, limit=20, sort_by='created_at', sort_order='desc'):
        """
        Get paginated list of notification logs with filters.
        """
        qs = NotificationLog.objects.all()
        
        if filters:
            if filters.get('status'):
                qs = qs.filter(status=filters['status'])
            if filters.get('recipient_email'):
                qs = qs.filter(recipient_email__icontains=filters['recipient_email'])
            if filters.get('from_date'):
                qs = qs.filter(created_at__gte=filters['from_date'])
            if filters.get('to_date'):
                qs = qs.filter(created_at__lte=filters['to_date'])
            if filters.get('search'):
                search = filters['search']
                qs = qs.filter(
                    Q(recipient_email__icontains=search) |
                    Q(subject__icontains=search) |
                    Q(payload__icontains=search)
                )
        
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
        Create a new notification log.
        """
        log_entry = NotificationLog.objects.create(
            recipient_email=data['recipient_email'],
            subject=data.get('subject'),
            payload=data.get('payload'),
            status=NotificationLog.Status.QUEUED
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_log_create',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'data': data}
            )
        
        logger.info(f"Notification log created: {log_entry.id}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def mark_as_sent(log_id, user=None, request=None):
        """
        Mark notification as sent.
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})
        
        log_entry.mark_as_sent()
        
        logger.info(f"Notification log marked as sent: {log_id}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def mark_as_failed(log_id, error_message, user=None, request=None):
        """
        Mark notification as failed.
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})
        
        log_entry.mark_as_failed(error_message)
        
        logger.info(f"Notification log marked as failed: {log_id}")
        return log_entry

    @staticmethod
    @transaction.atomic
    def retry_failed(log_id, user=None, request=None):
        """
        Retry a failed notification.
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})
        
        if log_entry.status not in [NotificationLog.Status.FAILED, NotificationLog.Status.QUEUED]:
            raise ValidationError({'id': f'Cannot retry notification with status {log_entry.status}.'})
        
        # Reset status to queued
        log_entry.status = NotificationLog.Status.QUEUED
        log_entry.error_message = None
        log_entry.retry_count += 1
        log_entry.save()
        
        logger.info(f"Notification queued for retry: {log_id}")
        return log_entry

    @staticmethod
    def get_statistics():
        """
        Get notification log statistics.
        """
        qs = NotificationLog.objects.all()
        
        total = qs.count()
        
        by_status = qs.values('status').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # Average retry count for failed
        failed_avg = qs.filter(
            status=NotificationLog.Status.FAILED
        ).aggregate(avg=Avg('retry_count'))['avg'] or 0
        
        # Last 24 hours
        twenty_four_hours_ago = timezone.now() - timezone.timedelta(hours=24)
        last_24h = qs.filter(created_at__gte=twenty_four_hours_ago).count()
        
        return {
            'total': total,
            'by_status': list(by_status),
            'avg_retry_failed': round(failed_avg, 2),
            'last_24h': last_24h,
        }