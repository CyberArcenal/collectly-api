# audit/services/log.py
import logging
from django.db import transaction
from django.utils import timezone
from audit.models.log import AuditLog
from audit.models.policy import AuditPolicy
from utils.pagination import paginate_queryset
import csv
import tempfile
import os
from django.core.files import File


logger = logging.getLogger(__name__)


class AuditLogService:
    """
    Service layer for AuditLog operations.
    Handles log creation, retrieval, and management.
    """

    # ======================================================================
    # GETTERS
    # ======================================================================

    @staticmethod
    def get_log_by_id(log_id: int) -> AuditLog:
        """
        Get an audit log by its primary key.

        Args:
            log_id: The log ID.

        Returns:
            AuditLog: The log instance.

        Raises:
            AuditLog.DoesNotExist: If log not found.
        """
        return AuditLog.objects.get(id=log_id)

    @staticmethod
    def get_log_by_event_id(event_id: str) -> AuditLog:
        """
        Get an audit log by its event UUID.

        Args:
            event_id: The event UUID.

        Returns:
            AuditLog: The log instance.

        Raises:
            AuditLog.DoesNotExist: If log not found.
        """
        return AuditLog.objects.get(event_id=event_id)

    @staticmethod
    def get_logs_by_user(user_id: int, limit: int = 100) -> list:
        """
        Get audit logs for a specific user.

        Args:
            user_id: The user ID.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(user_id=user_id).order_by('-timestamp')[:limit]

    @staticmethod
    def get_logs_by_action_type(action_type: str, limit: int = 100) -> list:
        """
        Get audit logs by action type.

        Args:
            action_type: The action type.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(action_type=action_type).order_by('-timestamp')[:limit]

    @staticmethod
    def get_suspicious_logs(limit: int = 50) -> list:
        """
        Get suspicious audit logs.

        Args:
            limit: Maximum number of logs to return.

        Returns:
            list: List of suspicious AuditLog objects.
        """
        return AuditLog.objects.filter(
            is_suspicious=True
        ).order_by('-timestamp')[:limit]

    @staticmethod
    def get_logs_by_date_range(start_date, end_date, limit: int = 100) -> list:
        """
        Get audit logs within a date range.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            limit: Maximum number of logs to return.

        Returns:
            list: List of AuditLog objects.
        """
        return AuditLog.objects.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).order_by('-timestamp')[:limit]

    # ======================================================================
    # CRUD OPERATIONS
    # ======================================================================

    @staticmethod
    @transaction.atomic
    def create_log(validated_data: dict) -> AuditLog:
        """
        Create a new audit log entry.

        Args:
            validated_data: Validated data from the serializer.

        Returns:
            AuditLog: The created log instance.

        Note:
            AuditLog entries are immutable and cannot be updated or deleted.
        """
        # Set timestamp to now if not provided
        if "timestamp" not in validated_data:
            validated_data["timestamp"] = timezone.now()

        log_entry = AuditLog.objects.create(**validated_data)

        logger.info(
            f"AuditLog created: event_id={log_entry.event_id}, "
            f"action={log_entry.action_type}, "
            f"model={log_entry.model_name}, "
            f"user={log_entry.user_id or 'System'}"
        )

        # Check retention policy and archive if needed
        AuditLogService._check_retention_policy(log_entry)

        return log_entry

    @staticmethod
    def _check_retention_policy(log_entry: AuditLog):
        """
        Check if log should be archived based on retention policy.

        Args:
            log_entry: The new AuditLog entry.
        """
        try:
            policy = AuditPolicy.objects.first()
            if policy:
                # Check if log is suspicious and should be flagged
                # This could trigger additional monitoring
                if log_entry.is_suspicious:
                    logger.warning(
                        f"Suspicious activity detected: {log_entry.action_type} "
                        f"on {log_entry.model_name} by {log_entry.user_id or 'System'}"
                    )
        except Exception as e:
            logger.error(f"Failed to check retention policy: {e}")

    # ======================================================================
    # STATISTICS
    # ======================================================================

    @staticmethod
    def get_log_statistics(days: int = 7) -> dict:
        """
        Get audit log statistics.

        Args:
            days: Number of days to look back.

        Returns:
            dict: Statistics about audit logs.
        """
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta

        start_date = timezone.now() - timedelta(days=days)

        total_logs = AuditLog.objects.filter(timestamp__gte=start_date).count()

        # Action type distribution
        action_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).values('action_type').annotate(
            count=Count('id')
        ).order_by('-count')

        # Model name distribution
        model_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).values('model_name').annotate(
            count=Count('id')
        ).order_by('-count')

        # Suspicious count
        suspicious_count = AuditLog.objects.filter(
            timestamp__gte=start_date,
            is_suspicious=True
        ).count()

        # User distribution
        user_distribution = AuditLog.objects.filter(
            timestamp__gte=start_date,
            user__isnull=False
        ).values('user__username').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        return {
            "total_logs": total_logs,
            "suspicious_count": suspicious_count,
            "days": days,
            "action_distribution": list(action_distribution),
            "model_distribution": list(model_distribution),
            "top_users": list(user_distribution),
        }

    @staticmethod
    def get_log_count_by_date(days: int = 7) -> list:
        """
        Get daily log counts for the specified period.

        Args:
            days: Number of days to look back.

        Returns:
            list: Daily log counts.
        """
        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.utils import timezone
        from datetime import timedelta

        start_date = timezone.now() - timedelta(days=days)

        daily_counts = AuditLog.objects.filter(
            timestamp__gte=start_date
        ).annotate(
            date=TruncDate('timestamp')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')

        return list(daily_counts)

    # ======================================================================
    # CLEANUP (Admin only)
    # ======================================================================

    @staticmethod
    @transaction.atomic
    def cleanup_old_logs(days: int = 90) -> int:
        """
        Delete audit logs older than the specified days.

        Args:
            days: Number of days to keep.

        Returns:
            int: Number of logs deleted.

        Warning:
            This is irreversible. Should only be done by admins.
        """
        from django.utils import timezone
        from datetime import timedelta

        # Check retention policy
        policy = AuditPolicy.objects.first()
        if policy and days < policy.retention_years * 365:
            raise ValueError(
                f"Cannot delete logs older than {days} days. "
                f"Retention policy requires {policy.retention_years} years."
            )

        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = AuditLog.objects.filter(
            timestamp__lt=cutoff_date
        ).delete()

        logger.info(f"Deleted {deleted_count} audit logs older than {days} days")

        return deleted_count
    
    # ============================================================
    # PAGINATED LIST WITH FILTERS
    # ============================================================

    @staticmethod
    def get_paginated_logs(
        filters=None,
        page=1,
        limit=50,
        sort_by='timestamp',
        sort_order='desc'
    ):
        """
        Get paginated list of audit logs with filters.
        
        Args:
            filters: Dictionary of filter criteria
            page: Page number for pagination
            limit: Number of items per page
            sort_by: Field to sort by
            sort_order: 'asc' or 'desc'
        
        Returns:
            dict: {
                'data': list of AuditLog objects,
                'pagination': pagination metadata
            }
        """
        from django.db.models import Q
        qs = AuditLog.objects.all().select_related('user')
        
        if filters:
            if filters.get('search_term'):
                search = filters['search_term']
                qs = qs.filter(
                    Q(action_type__icontains=search) |
                    Q(model_name__icontains=search) |
                    Q(object_id__icontains=search) |
                    Q(user_agent__icontains=search) |
                    Q(ip_address__icontains=search)
                )
            
            if filters.get('entity'):
                qs = qs.filter(model_name=filters['entity'])
            
            if filters.get('user'):
                qs = qs.filter(user__username__icontains=filters['user'])
            
            if filters.get('action'):
                qs = qs.filter(action_type=filters['action'])
            
            if filters.get('start_date'):
                qs = qs.filter(timestamp__gte=filters['start_date'])
            
            if filters.get('end_date'):
                qs = qs.filter(timestamp__lte=filters['end_date'])
            
            if filters.get('entity_id'):
                qs = qs.filter(object_id=str(filters['entity_id']))
        
        # Apply sorting
        if sort_order.lower() == 'asc':
            sort_by = sort_by
        else:
            sort_by = f'-{sort_by}'
        qs = qs.order_by(sort_by)
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # GET BY ENTITY
    # ============================================================

    @staticmethod
    def get_logs_by_entity(entity, entity_id=None, page=1, limit=50):
        """
        Get paginated audit logs for a specific entity.
        
        Args:
            entity: Entity name (e.g., 'Borrower', 'Debt')
            entity_id: Optional specific entity ID
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of audit logs
        """
        qs = AuditLog.objects.filter(model_name=entity).select_related('user')
        
        if entity_id is not None:
            qs = qs.filter(object_id=str(entity_id))
        
        qs = qs.order_by('-timestamp')
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # GET BY USER
    # ============================================================

    @staticmethod
    def get_logs_by_user(username, page=1, limit=50):
        """
        Get paginated audit logs for a specific user.
        
        Args:
            username: Username of the user
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of audit logs
        """
        qs = AuditLog.objects.filter(
            user__username__icontains=username
        ).select_related('user').order_by('-timestamp')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # GET BY ACTION
    # ============================================================

    @staticmethod
    def get_logs_by_action(action, page=1, limit=50):
        """
        Get paginated audit logs for a specific action type.
        
        Args:
            action: Action type (e.g., 'create', 'update', 'delete')
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of audit logs
        """
        qs = AuditLog.objects.filter(
            action_type=action
        ).select_related('user').order_by('-timestamp')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # GET BY DATE RANGE
    # ============================================================

    @staticmethod
    def get_logs_by_date_range(start_date, end_date, page=1, limit=50):
        """
        Get paginated audit logs within a date range.
        
        Args:
            start_date: Start date (ISO datetime)
            end_date: End date (ISO datetime)
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of audit logs
        """
        qs = AuditLog.objects.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date
        ).select_related('user').order_by('-timestamp')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # SEARCH
    # ============================================================

    @staticmethod
    def search_logs(search_term, page=1, limit=50):
        """
        Search audit logs by keyword.
        
        Args:
            search_term: Search keyword
            page: Page number for pagination
            limit: Number of items per page
        
        Returns:
            dict: Paginated list of audit logs
        """
        from django.db.models import Q
        
        qs = AuditLog.objects.filter(
            Q(action_type__icontains=search_term) |
            Q(model_name__icontains=search_term) |
            Q(object_id__icontains=search_term) |
            Q(user_agent__icontains=search_term) |
            Q(ip_address__icontains=search_term) |
            Q(user__username__icontains=search_term)
        ).select_related('user').order_by('-timestamp')
        
        return paginate_queryset(qs, page, limit)


    # ============================================================
    # SUMMARY (GROUPED COUNTS)
    # ============================================================

    @staticmethod
    def get_summary(start_date=None, end_date=None):
        """
        Get grouped summary of audit logs by action, entity, and user.
        
        Args:
            start_date: Optional start date
            end_date: Optional end date
        
        Returns:
            dict: {
                'by_action': [{'action': 'create', 'count': 10}, ...],
                'by_entity': [{'entity': 'Borrower', 'count': 5}, ...],
                'by_user': [{'user': 'admin', 'count': 8}, ...]
            }
        """
        from django.db.models import Count
        
        qs = AuditLog.objects.all()
        
        if start_date:
            qs = qs.filter(timestamp__gte=start_date)
        if end_date:
            qs = qs.filter(timestamp__lte=end_date)
        
        by_action = qs.values('action_type').annotate(
            count=Count('id')
        ).order_by('-count')
        
        by_entity = qs.values('model_name').annotate(
            count=Count('id')
        ).order_by('-count')
        
        by_user = qs.exclude(user__isnull=True).values(
            'user__username'
        ).annotate(
            count=Count('id')
        ).order_by('-count')
        
        return {
            'by_action': [
                {'action': item['action_type'], 'count': item['count']}
                for item in by_action
            ],
            'by_entity': [
                {'entity': item['model_name'], 'count': item['count']}
                for item in by_entity
            ],
            'by_user': [
                {'user': item['user__username'], 'count': item['count']}
                for item in by_user
            ],
        }


    # ============================================================
    # COUNTS (AGGREGATED)
    # ============================================================

    @staticmethod
    def get_counts(start_date=None, end_date=None):
        """
        Get aggregated counts grouped by action, entity, and user.
        Same as summary but with different key names for client compatibility.
        
        Args:
            start_date: Optional start date
            end_date: Optional end date
        
        Returns:
            dict: {
                'byAction': [{'action': 'create', 'count': 10}, ...],
                'byEntity': [{'entity': 'Borrower', 'count': 5}, ...],
                'byUser': [{'user': 'admin', 'count': 8}, ...]
            }
        """
        summary = AuditLogService.get_summary(start_date, end_date)
        
        return {
            'byAction': summary['by_action'],
            'byEntity': summary['by_entity'],
            'byUser': summary['by_user'],
        }


    # ============================================================
    # TOP ACTIVITIES
    # ============================================================

    @staticmethod
    def get_top_activities(limit=10, start_date=None, end_date=None):
        """
        Get top activities (most frequent actions, entities, users).
        
        Args:
            limit: Number of top items to return (max 20)
            start_date: Optional start date
            end_date: Optional end date
        
        Returns:
            dict: {
                'topActions': [{'action': 'create', 'count': 10}, ...],
                'topEntities': [{'entity': 'Borrower', 'count': 5}, ...],
                'topUsers': [{'user': 'admin', 'count': 8}, ...]
            }
        """
        from django.db.models import Count
        
        limit = min(limit, 20)
        
        qs = AuditLog.objects.all()
        
        if start_date:
            qs = qs.filter(timestamp__gte=start_date)
        if end_date:
            qs = qs.filter(timestamp__lte=end_date)
        
        top_actions = qs.values('action_type').annotate(
            count=Count('id')
        ).order_by('-count')[:limit]
        
        top_entities = qs.values('model_name').annotate(
            count=Count('id')
        ).order_by('-count')[:limit]
        
        top_users = qs.exclude(user__isnull=True).values(
            'user__username'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:limit]
        
        return {
            'topActions': [
                {'action': item['action_type'], 'count': item['count']}
                for item in top_actions
            ],
            'topEntities': [
                {'entity': item['model_name'], 'count': item['count']}
                for item in top_entities
            ],
            'topUsers': [
                {'user': item['user__username'], 'count': item['count']}
                for item in top_users
            ],
        }


    # ============================================================
    # RECENT ACTIVITY
    # ============================================================

    @staticmethod
    def get_recent_activity(limit=10):
        """
        Get recent activity (latest N audit logs).
        
        Args:
            limit: Number of entries (max 50)
        
        Returns:
            dict: {'items': list of AuditLog objects, 'limit': int}
        """
        limit = min(limit, 50)
        items = AuditLog.objects.all().select_related('user').order_by('-timestamp')[:limit]
        
        return {
            'items': list(items),
            'limit': limit,
        }


    # ============================================================
    # EXPORT LOGS (CSV)
    # ============================================================

    @staticmethod
    def export_logs_to_csv(filters=None, limit=5000):
        """
        Export audit logs to CSV.
        
        Args:
            filters: Optional filters (same as get_paginated_logs)
            limit: Max rows to export (max 10000)
        
        Returns:
            dict: {
                'filePath': str,
                'filename': str
            }
        """
        import csv
        import os
        import tempfile
        
        limit = min(limit, 10000)
        
        qs = AuditLog.objects.all().select_related('user').order_by('-timestamp')[:limit]
        
        if filters:
            from django.db.models import Q
            if filters.get('search_term'):
                search = filters['search_term']
                qs = qs.filter(
                    Q(action_type__icontains=search) |
                    Q(model_name__icontains=search) |
                    Q(object_id__icontains=search)
                )
            if filters.get('entity'):
                qs = qs.filter(model_name=filters['entity'])
            if filters.get('user'):
                qs = qs.filter(user__username__icontains=filters['user'])
            if filters.get('action'):
                qs = qs.filter(action_type=filters['action'])
            if filters.get('start_date'):
                qs = qs.filter(timestamp__gte=filters['start_date'])
            if filters.get('end_date'):
                qs = qs.filter(timestamp__lte=filters['end_date'])
        
        # Generate CSV
        temp_dir = tempfile.gettempdir()
        filename = f"audit_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file_path = os.path.join(temp_dir, filename)
        queryset = qs[:min(limit, 10000)]
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='w', newline='') as tmp:
            writer = csv.writer(tmp)
            writer.writerow(['ID', 'Action', 'Entity', 'EntityId', 'User', 'Timestamp', 'OldData', 'NewData'])
            for log in queryset:
                writer.writerow([
                    log.id,
                    log.action_type,
                    log.model_name,
                    log.object_id,
                    log.user.username if log.user else '',
                    log.timestamp.isoformat(),
                    log.changes.get('old') if log.changes else '',
                    log.changes.get('new') if log.changes else '',
                ])
            file_path = tmp.name
        return {
            'filePath': file_path,
            'filename': os.path.basename(file_path),
        }


    # ============================================================
    # GENERATE REPORT
    # ============================================================

    @staticmethod
    def generate_report(start_date=None, end_date=None, format='json'):
        """
        Generate a comprehensive audit report.
        
        Args:
            start_date: Optional start date
            end_date: Optional end date
            format: 'json' or 'html'
        
        Returns:
            dict: {
                'filePath': str,
                'format': str,
                'entryCount': int
            }
        """
        import json
        import os
        import tempfile
        
        qs = AuditLog.objects.all().select_related('user')
        
        if start_date:
            qs = qs.filter(timestamp__gte=start_date)
        if end_date:
            qs = qs.filter(timestamp__lte=end_date)
        
        logs = qs.order_by('-timestamp')
        total = logs.count()
        
        # Generate summary
        from django.db.models import Count
        by_action = logs.values('action_type').annotate(count=Count('id'))
        by_entity = logs.values('model_name').annotate(count=Count('id'))
        by_user = logs.exclude(user__isnull=True).values('user__username').annotate(count=Count('id'))
        
        report_data = {
            'generatedAt': timezone.now().isoformat(),
            'dateRange': {
                'start': start_date.isoformat() if start_date else None,
                'end': end_date.isoformat() if end_date else None,
            } if start_date or end_date else None,
            'total': total,
            'summary': {
                'byAction': list(by_action),
                'byEntity': list(by_entity),
                'byUser': list(by_user),
            },
            'logs': list(logs[:500].values(
                'id', 'event_id', 'action_type', 'model_name',
                'object_id', 'user__username', 'changes',
                'ip_address', 'is_suspicious', 'timestamp'
            )),
        }
        
        temp_dir = tempfile.gettempdir()
        
        if format == 'html':
            # Generate HTML report
            filename = f"audit_report_{timezone.now().strftime('%Y%m%d_%H%M%S')}.html"
            file_path = os.path.join(temp_dir, filename)
            
            html_content = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Audit Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .summary {{ margin-bottom: 20px; }}
            .summary-item {{ display: inline-block; margin-right: 30px; }}
        </style>
    </head>
    <body>
        <h1>Audit Log Report</h1>
        <p>Generated: {timezone.now().isoformat()}</p>
        <div class="summary">
            <div class="summary-item"><strong>Total Logs:</strong> {total}</div>
        </div>
        <h2>Summary</h2>
        <h3>By Action</h3>
        <ul>{"".join(f"<li>{item['action_type']}: {item['count']}</li>" for item in by_action)}</ul>
        <h3>By Entity</h3>
        <ul>{"".join(f"<li>{item['model_name']}: {item['count']}</li>" for item in by_entity)}</ul>
        <h3>By User</h3>
        <ul>{"".join(f"<li>{item['user__username']}: {item['count']}</li>" for item in by_user)}</ul>
        <h2>Logs (latest 500)</h2>
        <table>
            <tr>
                <th>ID</th><th>Event ID</th><th>Action</th>
                <th>Entity</th><th>Object ID</th><th>User</th><th>Timestamp</th>
            </tr>
            {"".join(f"<tr><td>{log['id']}</td><td>{log['event_id']}</td><td>{log['action_type']}</td><td>{log['model_name']}</td><td>{log['object_id']}</td><td>{log['user__username']}</td><td>{log['timestamp']}</td></tr>" for log in report_data['logs'])}
        </table>
    </body>
    </html>"""
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
        else:
            # JSON report
            filename = f"audit_report_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = os.path.join(temp_dir, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=2, default=str)
        
        return {
            'filePath': file_path,
            'format': format,
            'entryCount': total,
        }
        