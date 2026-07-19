import logging
from django.db import transaction
from django.db.models import Q, Avg, Count
from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.utils.log import log_audit_event
from notifications.models.notification_log import NotificationLog
from utils.helpers import camel_to_snake
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
        sort_by = camel_to_snake(sort_by)
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
        channel=data.get('channel', NotificationLog.Channel.EMAIL),
        recipient=data['recipient'],
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

    # ============================================================
    # GET BY RECIPIENT
    # ============================================================

    @staticmethod
    def get_by_recipient(recipient_email, page=1, limit=20):
        """
        Get paginated notification logs for a specific recipient.
        
        Args:
            recipient_email: Email address of the recipient
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of notification logs
        """
        if not recipient_email:
            raise ValidationError({'recipient_email': 'Recipient email is required.'})
        
        qs = NotificationLog.objects.filter(
            recipient_email__icontains=recipient_email
        ).order_by('-created_at')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # SEARCH
    # ============================================================

    @staticmethod
    def search(keyword, page=1, limit=20):
        """
        Search notification logs by keyword.
        
        Args:
            keyword: Search keyword (matches email, subject, or payload)
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of notification logs
        """
        if not keyword:
            raise ValidationError({'keyword': 'Search keyword is required.'})
        
        qs = NotificationLog.objects.filter(
            Q(recipient_email__icontains=keyword) |
            Q(subject__icontains=keyword) |
            Q(payload__icontains=keyword)
        ).order_by('-created_at')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # RESEND
    # ============================================================

    @staticmethod
    @transaction.atomic
    def resend(log_id, user=None, request=None):
        """
        Resend a notification (manual resend).
        
        Args:
            log_id: ID of the notification log to resend
            user: User performing the action
            request: HTTP request object
        
        Returns:
            NotificationLog: The updated notification log instance
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})
        
        # Reset status to queued for resend
        log_entry.status = NotificationLog.Status.QUEUED
        log_entry.resend_count += 1
        log_entry.error_message = None
        log_entry.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_resend',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'resend_count': log_entry.resend_count}
            )
        
        logger.info(f"Notification marked for resend: {log_id}")
        return log_entry


    # ============================================================
    # RETRY ALL FAILED
    # ============================================================

    @staticmethod
    @transaction.atomic
    def retry_all_failed(filters=None, user=None, request=None):
        """
        Retry all failed notifications with optional filters.
        
        Args:
            filters: Optional filters (recipient_email, created_before)
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {
                'processed': int,  # Number of notifications processed
                'errors': int,     # Number of errors
                'skipped': int,    # Number skipped
            }
        """
        qs = NotificationLog.objects.filter(
            status__in=[NotificationLog.Status.FAILED, NotificationLog.Status.QUEUED]
        )
        
        # Apply filters
        if filters:
            if filters.get('recipient_email'):
                qs = qs.filter(recipient_email__icontains=filters['recipient_email'])
            if filters.get('created_before'):
                qs = qs.filter(created_at__lte=filters['created_before'])
        
        total = qs.count()
        processed = 0
        errors = 0
        
        for log_entry in qs:
            try:
                # Reset for retry
                log_entry.status = NotificationLog.Status.QUEUED
                log_entry.retry_count += 1
                log_entry.error_message = None
                log_entry.save()
                processed += 1
            except Exception as e:
                logger.error(f"Failed to retry notification {log_entry.id}: {e}")
                errors += 1
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_retry_all_failed',
                model_name='NotificationLog',
                object_id='all_failed',
                changes={
                    'processed': processed,
                    'errors': errors,
                    'total': total,
                    'filters': filters
                }
            )
        
        logger.info(f"Retry all failed completed: {processed} processed, {errors} errors")
        return {
            'processed': processed,
            'errors': errors,
            'skipped': total - processed - errors,
        }


    # ============================================================
    # SEND NOTIFICATION (for reminder logs)
    # ============================================================

    @staticmethod
    @transaction.atomic
    def send_notification(data, user=None, request=None):
        """
        Create and send a new notification (for reminder logs).
        
        Args:
            data: Dictionary with 'to', 'subject', 'html', 'text'
            user: User performing the action
            request: HTTP request object
        
        Returns:
            NotificationLog: The created notification log
        """
        to = data.get('to')
        if not to:
            raise ValidationError({'to': 'Recipient email is required.'})
        
        subject = data.get('subject', 'Notification')
        html = data.get('html')
        text = data.get('text')
        payload = html or text or ''
        
        log_entry = NotificationLog.objects.create(
            recipient_email=to,
            subject=subject,
            payload=payload,
            status=NotificationLog.Status.QUEUED
        )
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='reminder_send',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'data': data}
            )
        
        logger.info(f"Reminder notification created: {log_entry.id}")
        return log_entry


    # ============================================================
    # UPDATE STATUS (for reminder logs)
    # ============================================================

    @staticmethod
    @transaction.atomic
    def update_status(log_id, status, error_message=None, user=None, request=None):
        """
        Update the status of a notification log.
        
        Args:
            log_id: ID of the notification log
            status: New status ('queued', 'sent', 'failed', 'resend')
            error_message: Error message if status is 'failed'
            user: User performing the action
            request: HTTP request object
        
        Returns:
            NotificationLog: The updated notification log
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})
        
        valid_statuses = ['queued', 'sent', 'failed', 'resend']
        if status not in valid_statuses:
            raise ValidationError({'status': f'Invalid status. Must be one of {valid_statuses}'})
        
        log_entry.status = status
        
        if status == 'sent':
            log_entry.sent_at = timezone.now()
            log_entry.error_message = None
        elif status == 'failed':
            if not error_message:
                raise ValidationError({'error_message': 'Error message is required when marking as failed.'})
            log_entry.last_error_at = timezone.now()
            log_entry.error_message = error_message
            log_entry.retry_count += 1
        elif status == 'resend':
            log_entry.sent_at = timezone.now()
            log_entry.resend_count += 1
            log_entry.error_message = None
        
        log_entry.save()
        
        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_update_status',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'status': status, 'error_message': error_message}
            )
        
        logger.info(f"Notification log status updated: {log_id} → {status}")
        return log_entry
    
    @staticmethod
    @transaction.atomic
    def delete(log_id, user=None, request=None):
        """
        Soft delete a notification log.

        Args:
            log_id: ID of the notification log to delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            NotificationLog: The soft-deleted log instance

        Raises:
            ValidationError: If log not found or already deleted
        """
        log_entry = NotificationLogService.get_by_id(log_id)
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})

        if log_entry.deleted_at:
            raise ValidationError({'id': 'Notification log is already deleted.'})

        log_entry.soft_delete()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_log_delete',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'deleted_at': log_entry.deleted_at}
            )

        logger.info(f"Notification log soft-deleted: {log_id}")
        return log_entry
    
    @staticmethod
    @transaction.atomic
    def restore(log_id, user=None, request=None):
        """
        Restore a soft-deleted notification log.

        Args:
            log_id: ID of the notification log to restore
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Returns:
            NotificationLog: The restored log instance

        Raises:
            ValidationError: If log not found or not deleted
        """
        from notifications.models.notification_log import NotificationLog

        log_entry = NotificationLog.objects.filter(id=log_id).first()
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})

        if not log_entry.deleted_at:
            raise ValidationError({'id': 'Notification log is not deleted.'})

        log_entry.restore()

        # Audit log
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_log_restore',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'restored_at': timezone.now()}
            )

        logger.info(f"Notification log restored: {log_id}")
        return log_entry
    
    @staticmethod
    @transaction.atomic
    def permanent_delete(log_id, user=None, request=None):
        """
        Permanently delete a notification log (hard delete).

        Args:
            log_id: ID of the notification log to permanently delete
            user: User performing the action (for audit)
            request: HTTP request object (for audit)

        Raises:
            ValidationError: If log not found
        """
        from notifications.models.notification_log import NotificationLog

        log_entry = NotificationLog.objects.filter(id=log_id).first()
        if not log_entry:
            raise ValidationError({'id': 'Notification log not found.'})

        # Audit log before deletion
        if user:
            log_audit_event(
                request=request,
                user=user,
                action_type='notification_log_permanent_delete',
                model_name='NotificationLog',
                object_id=str(log_entry.id),
                changes={'permanent': True}
            )

        log_entry.delete()
        logger.info(f"Notification log permanently deleted: {log_id}")