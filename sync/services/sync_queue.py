# sync/services/sync_queue.py
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from django.db import transaction
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Count, Sum

from sync.models.sync_queue import SyncQueue
from sync.models.sync_metadata import SyncMetadata
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class SyncQueueService:
    """
    Service for managing sync queue items.
    
    Handles:
    - Enqueuing sync items (create, update, delete)
    - Processing queue items with retry logic
    - Tracking retry attempts
    - Queue statistics and cleanup
    """
    
    # ============================================================
    # CREATE / ENQUEUE
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def enqueue(
        entity: str,
        entity_id: int,
        action: str,
        data: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
        user: Optional[str] = None,
        request=None,
    ) -> SyncQueue:
        """
        Enqueue a record for sync.
        
        Args:
            entity: Entity name (e.g., 'Borrower', 'Debt')
            entity_id: ID of the record
            action: 'create', 'update', 'delete'
            data: Record data (for create/update)
            max_retries: Maximum retry attempts
            user: User performing the action
            request: HTTP request object
        
        Returns:
            SyncQueue: The created/updated queue item
        
        Raises:
            ValidationError: If invalid action or missing data
        """
        valid_actions = ['create', 'update', 'delete']
        if action not in valid_actions:
            raise ValidationError({
                'action': f'Invalid action: {action}. Must be one of {valid_actions}.'
            })
        
        if action in ['create', 'update'] and data is None:
            raise ValidationError({
                'data': f'Data is required for "{action}" action.'
            })
        
        # Check if there's already a pending/processing item for this entity/id
        existing = SyncQueue.objects.filter(
            entity=entity,
            entity_id=entity_id,
            status__in=[SyncQueue.Status.PENDING, SyncQueue.Status.PROCESSING],
        ).first()
        
        if existing:
            # Update existing instead of creating new
            existing.action = action
            existing.data = data
            existing.updated_at = timezone.now()
            existing.save(update_fields=['action', 'data', 'updated_at'])
            
            logger.debug(f"[SyncQueue] Updated existing queue item for {entity}#{entity_id}")
            
            # Audit log
            if user and request:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type='sync_queue_updated',
                    model_name='SyncQueue',
                    object_id=str(existing.id),
                    changes={'entity': entity, 'entity_id': entity_id, 'action': action},
                )
            
            return existing
        
        # Create new queue item
        queue_item = SyncQueue.objects.create(
            entity=entity,
            entity_id=entity_id,
            action=action,
            data=data,
            status=SyncQueue.Status.PENDING,
            max_retries=max_retries,
        )
        
        logger.info(f"[SyncQueue] Enqueued {action} for {entity}#{entity_id}")
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_queue_created',
                model_name='SyncQueue',
                object_id=str(queue_item.id),
                changes={'entity': entity, 'entity_id': entity_id, 'action': action},
            )
        
        return queue_item
    
    @staticmethod
    @transaction.atomic
    def enqueue_batch(
        items: List[Dict[str, Any]],
        max_retries: int = 5,
        user: Optional[str] = None,
        request=None,
    ) -> Dict[str, Any]:
        """
        Enqueue multiple items in batch.
        
        Args:
            items: List of dicts with keys: entity, entity_id, action, data
            max_retries: Maximum retry attempts
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'created': int, 'updated': int, 'errors': list}
        """
        results = {
            'created': 0,
            'updated': 0,
            'errors': [],
            'items': [],
        }
        
        for item in items:
            try:
                queue_item = SyncQueueService.enqueue(
                    entity=item.get('entity'),
                    entity_id=item.get('entity_id'),
                    action=item.get('action'),
                    data=item.get('data'),
                    max_retries=item.get('max_retries', max_retries),
                    user=user,
                    request=request,
                )
                
                # Check if it was created or updated
                if queue_item.created_at == queue_item.updated_at:
                    results['created'] += 1
                else:
                    results['updated'] += 1
                
                results['items'].append({
                    'id': queue_item.id,
                    'entity': queue_item.entity,
                    'entity_id': queue_item.entity_id,
                    'action': queue_item.action,
                    'status': queue_item.status,
                })
                
            except Exception as e:
                results['errors'].append({
                    'item': item,
                    'error': str(e),
                })
        
        logger.info(f"[SyncQueue] Batch enqueued: {results['created']} created, {results['updated']} updated")
        return results
    
    # ============================================================
    # READ / RETRIEVE
    # ============================================================
    
    @staticmethod
    def get_by_id(queue_id: int) -> Optional[SyncQueue]:
        """
        Get a queue item by ID.
        
        Args:
            queue_id: ID of the queue item
        
        Returns:
            SyncQueue instance or None if not found
        """
        try:
            return SyncQueue.objects.get(id=queue_id)
        except SyncQueue.DoesNotExist:
            return None
    
    @staticmethod
    def get_pending(limit: int = 100) -> List[SyncQueue]:
        """
        Get pending queue items (pending or failed with retry available).
        
        Args:
            limit: Maximum number of items
        
        Returns:
            List of SyncQueue instances
        """
        return SyncQueue.objects.filter(
            status__in=[SyncQueue.Status.PENDING, SyncQueue.Status.FAILED],
            retry_count__lt=models.F('max_retries'),
        ).order_by('created_at')[:limit]
    
    @staticmethod
    def get_by_status(
        status: str,
        limit: int = 100,
        entity: Optional[str] = None,
    ) -> List[SyncQueue]:
        """
        Get queue items by status.
        
        Args:
            status: 'pending', 'processing', 'completed', 'failed'
            limit: Maximum number of items
            entity: Optional filter by entity
        
        Returns:
            List of SyncQueue instances
        """
        qs = SyncQueue.objects.filter(status=status)
        if entity:
            qs = qs.filter(entity=entity)
        return qs.order_by('-created_at')[:limit]
    
    @staticmethod
    def get_by_entity(
        entity: str,
        entity_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[SyncQueue]:
        """
        Get queue items for a specific entity.
        
        Args:
            entity: Entity name
            entity_id: Optional specific record ID
            status: Optional filter by status
            limit: Maximum number of items
        
        Returns:
            List of SyncQueue instances
        """
        qs = SyncQueue.objects.filter(entity=entity)
        
        if entity_id:
            qs = qs.filter(entity_id=entity_id)
        
        if status:
            qs = qs.filter(status=status)
        
        return qs.order_by('-created_at')[:limit]
    
    @staticmethod
    def get_next_pending(entity: Optional[str] = None) -> Optional[SyncQueue]:
        """
        Get the next pending queue item (oldest first).
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            SyncQueue instance or None
        """
        qs = SyncQueue.objects.filter(
            status=SyncQueue.Status.PENDING,
        ).order_by('created_at')
        
        if entity:
            qs = qs.filter(entity=entity)
        
        return qs.first()
    
    @staticmethod
    def count_pending(entity: Optional[str] = None) -> int:
        """
        Count pending queue items.
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            int: Number of pending items
        """
        qs = SyncQueue.objects.filter(
            status__in=[SyncQueue.Status.PENDING, SyncQueue.Status.FAILED],
            retry_count__lt=models.F('max_retries'),
        )
        
        if entity:
            qs = qs.filter(entity=entity)
        
        return qs.count()
    
    @staticmethod
    def has_pending(entity: str, entity_id: int) -> bool:
        """
        Check if a specific record has pending queue items.
        
        Args:
            entity: Entity name
            entity_id: Record ID
        
        Returns:
            bool: True if there are pending items
        """
        return SyncQueue.objects.filter(
            entity=entity,
            entity_id=entity_id,
            status__in=[SyncQueue.Status.PENDING, SyncQueue.Status.FAILED],
            retry_count__lt=models.F('max_retries'),
        ).exists()
    
    # ============================================================
    # STATISTICS
    # ============================================================
    
    @staticmethod
    def get_statistics(entity: Optional[str] = None) -> Dict[str, Any]:
        """
        Get queue statistics.
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            dict: Queue statistics
        """
        qs = SyncQueue.objects.all()
        if entity:
            qs = qs.filter(entity=entity)
        
        total = qs.count()
        
        # Count by status
        by_status = qs.values('status').annotate(count=Count('id'))
        status_stats = {
            item['status']: item['count']
            for item in by_status
        }
        
        # Average retry count for failed items
        failed_items = SyncQueue.objects.filter(status=SyncQueue.Status.FAILED)
        if entity:
            failed_items = failed_items.filter(entity=entity)
        
        avg_retry = failed_items.aggregate(avg=Sum('retry_count') / Count('id'))['avg'] or 0
        
        # Items that can still be retried
        retryable = qs.filter(
            status__in=[SyncQueue.Status.PENDING, SyncQueue.Status.FAILED],
            retry_count__lt=models.F('max_retries'),
        ).count()
        
        return {
            'total': total,
            'pending': status_stats.get(SyncQueue.Status.PENDING, 0),
            'processing': status_stats.get(SyncQueue.Status.PROCESSING, 0),
            'completed': status_stats.get(SyncQueue.Status.COMPLETED, 0),
            'failed': status_stats.get(SyncQueue.Status.FAILED, 0),
            'avg_retry_failed': round(float(avg_retry), 2),
            'retryable': retryable,
            'max_retries': 5,  # Default max retries
        }
    
    @staticmethod
    def get_entity_summary() -> List[Dict[str, Any]]:
        """
        Get queue summary by entity.
        
        Returns:
            list: Summary per entity
        """
        results = SyncQueue.objects.values('entity').annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status=SyncQueue.Status.PENDING)),
            processing=Count('id', filter=Q(status=SyncQueue.Status.PROCESSING)),
            completed=Count('id', filter=Q(status=SyncQueue.Status.COMPLETED)),
            failed=Count('id', filter=Q(status=SyncQueue.Status.FAILED)),
        ).order_by('-total')
        
        return [
            {
                'entity': item['entity'],
                'total': item['total'],
                'pending': item['pending'],
                'processing': item['processing'],
                'completed': item['completed'],
                'failed': item['failed'],
            }
            for item in results
        ]
    
    # ============================================================
    # PROCESSING
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def mark_processing(queue_id: int) -> SyncQueue:
        """
        Mark a queue item as being processed.
        
        Args:
            queue_id: ID of the queue item
        
        Returns:
            SyncQueue: Updated instance
        
        Raises:
            ValidationError: If item not found
        """
        item = SyncQueueService.get_by_id(queue_id)
        if not item:
            raise ValidationError({'queue_id': f'Queue item {queue_id} not found.'})
        
        if item.status != SyncQueue.Status.PENDING:
            raise ValidationError({
                'queue_id': f'Queue item {queue_id} is not pending (status: {item.status}).'
            })
        
        item.mark_processing()
        logger.debug(f"[SyncQueue] Marked {queue_id} as processing")
        return item
    
    @staticmethod
    @transaction.atomic
    def mark_completed(queue_id: int) -> SyncQueue:
        """
        Mark a queue item as completed.
        
        Args:
            queue_id: ID of the queue item
        
        Returns:
            SyncQueue: Updated instance
        
        Raises:
            ValidationError: If item not found
        """
        item = SyncQueueService.get_by_id(queue_id)
        if not item:
            raise ValidationError({'queue_id': f'Queue item {queue_id} not found.'})
        
        if item.status not in [SyncQueue.Status.PENDING, SyncQueue.Status.PROCESSING]:
            raise ValidationError({
                'queue_id': f'Queue item {queue_id} cannot be completed (status: {item.status}).'
            })
        
        item.mark_completed()
        logger.debug(f"[SyncQueue] Marked {queue_id} as completed")
        return item
    
    @staticmethod
    @transaction.atomic
    def mark_failed(
        queue_id: int,
        error_message: str,
    ) -> SyncQueue:
        """
        Mark a queue item as failed (increments retry count).
        
        Args:
            queue_id: ID of the queue item
            error_message: Error description
        
        Returns:
            SyncQueue: Updated instance
        
        Raises:
            ValidationError: If item not found
        """
        item = SyncQueueService.get_by_id(queue_id)
        if not item:
            raise ValidationError({'queue_id': f'Queue item {queue_id} not found.'})
        
        if item.status not in [SyncQueue.Status.PENDING, SyncQueue.Status.PROCESSING]:
            raise ValidationError({
                'queue_id': f'Queue item {queue_id} cannot be failed (status: {item.status}).'
            })
        
        item.mark_failed(error_message)
        
        if item.status == SyncQueue.Status.FAILED:
            logger.warning(f"[SyncQueue] Queue item {queue_id} permanently failed after {item.retry_count} retries")
        else:
            logger.debug(f"[SyncQueue] Queue item {queue_id} failed, retry {item.retry_count}/{item.max_retries}")
        
        return item
    
    @staticmethod
    @transaction.atomic
    def reset_for_retry(queue_id: int) -> SyncQueue:
        """
        Reset a failed queue item for retry.
        
        Args:
            queue_id: ID of the queue item
        
        Returns:
            SyncQueue: Updated instance
        
        Raises:
            ValidationError: If item not found or not failed
        """
        item = SyncQueueService.get_by_id(queue_id)
        if not item:
            raise ValidationError({'queue_id': f'Queue item {queue_id} not found.'})
        
        if item.status != SyncQueue.Status.FAILED:
            raise ValidationError({
                'queue_id': f'Queue item {queue_id} is not failed (status: {item.status}).'
            })
        
        if not item.can_retry():
            raise ValidationError({
                'queue_id': f'Queue item {queue_id} cannot be retried (max retries reached).'
            })
        
        item.reset_for_retry()
        logger.info(f"[SyncQueue] Reset queue item {queue_id} for retry")
        return item
    
    # ============================================================
    # PROCESS WITH HANDLER
    # ============================================================
    
    @staticmethod
    def process_item(
        queue_item: SyncQueue,
        handler: Callable,
        user: Optional[str] = None,
        request=None,
    ) -> Dict[str, Any]:
        """
        Process a single queue item with a handler function.
        
        Args:
            queue_item: SyncQueue instance
            handler: Async function that takes (item) and returns {success: bool, error?: str}
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'success': bool, 'item': dict, 'error': str, 'will_retry': bool}
        """
        try:
            # Mark as processing
            SyncQueueService.mark_processing(queue_item.id)
            
            # Execute handler
            result = handler(queue_item)
            
            if result.get('success', False):
                # Mark as completed
                SyncQueueService.mark_completed(queue_item.id)
                
                # Audit log
                if user and request:
                    log_audit_event(
                        request=request,
                        user=user,
                        action_type='sync_queue_processed',
                        model_name='SyncQueue',
                        object_id=str(queue_item.id),
                        changes={
                            'entity': queue_item.entity,
                            'entity_id': queue_item.entity_id,
                            'action': queue_item.action,
                            'status': 'completed',
                        },
                    )
                
                return {
                    'success': True,
                    'item': {
                        'id': queue_item.id,
                        'entity': queue_item.entity,
                        'entity_id': queue_item.entity_id,
                        'action': queue_item.action,
                    },
                    'error': None,
                    'will_retry': False,
                }
            else:
                # Mark as failed
                error_message = result.get('error', 'Unknown error')
                SyncQueueService.mark_failed(queue_item.id, error_message)
                
                return {
                    'success': False,
                    'item': {
                        'id': queue_item.id,
                        'entity': queue_item.entity,
                        'entity_id': queue_item.entity_id,
                        'action': queue_item.action,
                    },
                    'error': error_message,
                    'will_retry': queue_item.can_retry(),
                }
                
        except Exception as e:
            error_message = str(e)
            SyncQueueService.mark_failed(queue_item.id, error_message)
            
            return {
                'success': False,
                'item': {
                    'id': queue_item.id,
                    'entity': queue_item.entity,
                    'entity_id': queue_item.entity_id,
                    'action': queue_item.action,
                },
                'error': error_message,
                'will_retry': queue_item.can_retry(),
            }
    
    @staticmethod
    @transaction.atomic
    def process_all(
        handler: Callable,
        limit: int = 50,
        entity: Optional[str] = None,
        user: Optional[str] = None,
        request=None,
    ) -> Dict[str, Any]:
        """
        Process all pending queue items.
        
        Args:
            handler: Function that takes (item) and returns {success: bool, error?: str}
            limit: Maximum number of items to process
            entity: Optional filter by entity
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: Process results
        """
        # Get pending items
        items = SyncQueueService.get_pending(limit)
        
        if entity:
            items = [item for item in items if item.entity == entity]
        
        results = {
            'total': len(items),
            'processed': 0,
            'completed': 0,
            'failed': 0,
            'will_retry': 0,
            'errors': [],
            'items': [],
        }
        
        for item in items:
            result = SyncQueueService.process_item(item, handler, user, request)
            results['processed'] += 1
            
            if result['success']:
                results['completed'] += 1
            else:
                results['failed'] += 1
                if result['will_retry']:
                    results['will_retry'] += 1
                results['errors'].append({
                    'id': item.id,
                    'entity': item.entity,
                    'entity_id': item.entity_id,
                    'error': result['error'],
                    'will_retry': result['will_retry'],
                })
            
            results['items'].append(result['item'])
        
        logger.info(f"[SyncQueue] Processed {results['processed']} items: {results['completed']} completed, {results['failed']} failed")
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_queue_process_all',
                model_name='SyncQueue',
                object_id='batch',
                changes={
                    'total': results['total'],
                    'completed': results['completed'],
                    'failed': results['failed'],
                },
            )
        
        return results
    
    # ============================================================
    # DELETE / CLEANUP
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def delete_queue_item(queue_id: int, user: Optional[str] = None, request=None) -> bool:
        """
        Delete a queue item.
        
        Args:
            queue_id: ID of the queue item
            user: User performing the action
            request: HTTP request object
        
        Returns:
            bool: True if deleted, False if not found
        """
        item = SyncQueueService.get_by_id(queue_id)
        if not item:
            return False
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_queue_deleted',
                model_name='SyncQueue',
                object_id=str(item.id),
                changes={'entity': item.entity, 'entity_id': item.entity_id},
            )
        
        item.delete()
        logger.debug(f"[SyncQueue] Deleted queue item {queue_id}")
        return True
    
    @staticmethod
    @transaction.atomic
    def clear_entity(entity: str, user: Optional[str] = None, request=None) -> int:
        """
        Clear all queue items for an entity.
        
        Args:
            entity: Entity name
            user: User performing the action
            request: HTTP request object
        
        Returns:
            int: Number of items deleted
        """
        items = SyncQueue.objects.filter(entity=entity)
        count = items.count()
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_queue_clear_entity',
                model_name='SyncQueue',
                object_id=f'entity_{entity}',
                changes={'entity': entity, 'count': count},
            )
        
        items.delete()
        logger.info(f"[SyncQueue] Cleared {count} queue items for {entity}")
        return count
    
    @staticmethod
    @transaction.atomic
    def cleanup_completed(days: int = 30) -> int:
        """
        Delete completed/failed queue items older than given days.
        
        Args:
            days: Age in days
        
        Returns:
            int: Number of items deleted
        """
        cutoff = timezone.now() - timezone.timedelta(days=days)
        
        deleted, _ = SyncQueue.objects.filter(
            status__in=[SyncQueue.Status.COMPLETED, SyncQueue.Status.FAILED],
            processed_at__lt=cutoff,
        ).delete()
        
        logger.info(f"[SyncQueue] Cleaned up {deleted} old queue items")
        return deleted
    
    # ============================================================
    # UTILITY
    # ============================================================
    
    @staticmethod
    def can_retry(item: SyncQueue) -> bool:
        """
        Check if a queue item can be retried.
        
        Args:
            item: SyncQueue instance
        
        Returns:
            bool: True if retryable
        """
        return item.status == SyncQueue.Status.FAILED and item.can_retry()
    
    @staticmethod
    def is_pending(item: SyncQueue) -> bool:
        """
        Check if a queue item is pending (ready for processing).
        
        Args:
            item: SyncQueue instance
        
        Returns:
            bool: True if pending
        """
        return item.is_pending
    
    @staticmethod
    def get_retry_status(item: SyncQueue) -> Dict[str, Any]:
        """
        Get retry status for a queue item.
        
        Args:
            item: SyncQueue instance
        
        Returns:
            dict: Retry status details
        """
        return {
            'id': item.id,
            'retry_count': item.retry_count,
            'max_retries': item.max_retries,
            'can_retry': item.can_retry(),
            'remaining_retries': item.max_retries - item.retry_count,
            'status': item.status,
        }
    
    @staticmethod
    def format_item(item: SyncQueue) -> Dict[str, Any]:
        """
        Format a queue item for display.
        
        Args:
            item: SyncQueue instance
        
        Returns:
            dict: Formatted item
        """
        return {
            'id': item.id,
            'entity': item.entity,
            'entity_id': item.entity_id,
            'action': item.action,
            'action_display': item.get_action_display(),
            'status': item.status,
            'status_display': item.get_status_display(),
            'retry_count': item.retry_count,
            'max_retries': item.max_retries,
            'can_retry': item.can_retry(),
            'error_message': item.error_message,
            'processed_at': item.processed_at.isoformat() if item.processed_at else None,
            'created_at': item.created_at.isoformat(),
            'updated_at': item.updated_at.isoformat(),
            'data_preview': list(item.data.keys()) if item.data else [],
        }