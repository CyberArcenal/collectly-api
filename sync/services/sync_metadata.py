# sync/services/sync_metadata.py
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Count, Sum

from sync.models.sync_metadata import SyncMetadata
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class SyncMetadataService:
    """
    Service for managing sync metadata.
    
    Handles:
    - Tracking sync status per entity
    - Updating sync timestamps and counts
    - Getting sync status and summaries
    - Resetting sync states
    """
    
    # ============================================================
    # ENTITY DEFINITIONS
    # ============================================================
    
    # Define all entities that can be synced
    ENTITIES = [
        'Borrower',
        'Debt',
        'PaymentTransaction',
        'PenaltyTransaction',
        'LoanAgreement',
        'LoanApplication',
        'PaymentMethod',
    ]
    
    # ============================================================
    # INITIALIZATION
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def initialize_entities(entities: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Initialize sync metadata for all or specific entities.
        
        Args:
            entities: Optional list of entity names. If None, uses all.
        
        Returns:
            dict: {'created': int, 'skipped': int, 'entities': list}
        """
        if entities is None:
            entities = SyncMetadataService.ENTITIES
        
        created = 0
        skipped = 0
        entity_list = []
        
        for entity_name in entities:
            metadata, is_created = SyncMetadata.objects.get_or_create(
                entity=entity_name,
                defaults={
                    'status': SyncMetadata.Status.IDLE,
                    'last_synced_at': None,
                    'last_sync_count': 0,
                    'total_synced': 0,
                }
            )
            
            if is_created:
                created += 1
            else:
                skipped += 1
            
            entity_list.append({
                'entity': metadata.entity,
                'status': metadata.status,
                'created': is_created,
            })
        
        logger.info(f"[SyncMetadata] Initialized {created} entities (skipped {skipped})")
        return {
            'created': created,
            'skipped': skipped,
            'entities': entity_list,
        }
    
    # ============================================================
    # READ / RETRIEVE
    # ============================================================
    
    @staticmethod
    def get_by_entity(entity: str) -> Optional[SyncMetadata]:
        """
        Get sync metadata for a specific entity.
        
        Args:
            entity: Entity name
        
        Returns:
            SyncMetadata instance or None if not found
        """
        try:
            return SyncMetadata.objects.get(entity=entity)
        except SyncMetadata.DoesNotExist:
            return None
    
    @staticmethod
    def get_all(include_deleted: bool = False) -> List[SyncMetadata]:
        """
        Get sync metadata for all entities.
        
        Args:
            include_deleted: Whether to include soft-deleted records
        
        Returns:
            List of SyncMetadata instances
        """
        qs = SyncMetadata.objects.all()
        if not include_deleted:
            qs = qs.filter(deleted_at__isnull=True)
        return qs.order_by('entity')
    
    @staticmethod
    def get_by_status(status: str) -> List[SyncMetadata]:
        """
        Get entities by sync status.
        
        Args:
            status: 'idle', 'syncing', 'completed', 'failed'
        
        Returns:
            List of SyncMetadata instances
        """
        return SyncMetadata.objects.filter(
            status=status,
            deleted_at__isnull=True,
        ).order_by('entity')
    
    @staticmethod
    def get_pending() -> List[SyncMetadata]:
        """
        Get entities with pending sync (syncing or failed).
        
        Returns:
            List of SyncMetadata instances
        """
        return SyncMetadata.objects.filter(
            status__in=[SyncMetadata.Status.SYNCING, SyncMetadata.Status.FAILED],
            deleted_at__isnull=True,
        ).order_by('entity')
    
    @staticmethod
    def get_last_sync_time(entity: str) -> Optional[datetime]:
        """
        Get last sync time for an entity.
        
        Args:
            entity: Entity name
        
        Returns:
            datetime or None if not found
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        if metadata:
            return metadata.last_synced_at
        return None
    
    @staticmethod
    def get_sync_status(entity: str) -> Dict[str, Any]:
        """
        Get detailed sync status for an entity.
        
        Args:
            entity: Entity name
        
        Returns:
            dict: Status details
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        
        if not metadata:
            return {
                'entity': entity,
                'exists': False,
                'status': 'idle',
                'last_synced_at': None,
                'last_sync_count': 0,
                'total_synced': 0,
                'has_pending': False,
                'error_message': None,
            }
        
        return {
            'entity': metadata.entity,
            'exists': True,
            'status': metadata.status,
            'last_synced_at': metadata.last_synced_at,
            'last_sync_count': metadata.last_sync_count,
            'total_synced': metadata.total_synced,
            'has_pending': metadata.status in [
                SyncMetadata.Status.SYNCING,
                SyncMetadata.Status.FAILED,
            ],
            'error_message': metadata.error_message,
            'last_sync_started_at': metadata.last_sync_started_at,
            'created_at': metadata.created_at,
            'updated_at': metadata.updated_at,
        }
    
    # ============================================================
    # STATISTICS
    # ============================================================
    
    @staticmethod
    def get_statistics() -> Dict[str, Any]:
        """
        Get overall sync statistics.
        
        Returns:
            dict: Statistics across all entities
        """
        qs = SyncMetadata.objects.filter(deleted_at__isnull=True)
        
        total = qs.count()
        status_counts = qs.values('status').annotate(count=Count('id'))
        
        by_status = {
            item['status']: item['count']
            for item in status_counts
        }
        
        # Total records synced
        total_synced = qs.aggregate(total=Sum('total_synced'))['total'] or 0
        
        # Entities with errors
        with_errors = qs.filter(
            error_message__isnull=False,
            error_message__gt='',
        ).count()
        
        return {
            'total_entities': total,
            'total_synced': total_synced,
            'by_status': {
                'idle': by_status.get(SyncMetadata.Status.IDLE, 0),
                'syncing': by_status.get(SyncMetadata.Status.SYNCING, 0),
                'completed': by_status.get(SyncMetadata.Status.COMPLETED, 0),
                'failed': by_status.get(SyncMetadata.Status.FAILED, 0),
            },
            'with_errors': with_errors,
            'last_sync': None,  # Will be computed below
        }
    
    @staticmethod
    def get_summary() -> Dict[str, Any]:
        """
        Get a quick summary of sync status.
        
        Returns:
            dict: Summary with counts
        """
        qs = SyncMetadata.objects.filter(deleted_at__isnull=True)
        
        total = qs.count()
        
        # Count by status
        status_counts = qs.values('status').annotate(count=Count('id'))
        by_status = {
            item['status']: item['count']
            for item in status_counts
        }
        
        # Total synced
        total_synced = qs.aggregate(total=Sum('total_synced'))['total'] or 0
        
        # Pending count (syncing + failed)
        pending = by_status.get(SyncMetadata.Status.SYNCING, 0) + by_status.get(SyncMetadata.Status.FAILED, 0)
        
        # Completed count
        completed = by_status.get(SyncMetadata.Status.COMPLETED, 0)
        
        # Idle count
        idle = by_status.get(SyncMetadata.Status.IDLE, 0)
        
        # Failed count
        failed = by_status.get(SyncMetadata.Status.FAILED, 0)
        
        # Get latest sync
        latest = qs.exclude(last_synced_at__isnull=True).order_by('-last_synced_at').first()
        last_sync = latest.last_synced_at if latest else None
        
        return {
            'total_entities': total,
            'total_synced': total_synced,
            'pending': pending,
            'failed': failed,
            'completed': completed,
            'idle': idle,
            'last_sync': last_sync,
            'is_syncing': by_status.get(SyncMetadata.Status.SYNCING, 0) > 0,
        }
    
    @staticmethod
    def get_entity_statuses() -> List[Dict[str, Any]]:
        """
        Get status for all entities as a list.
        
        Returns:
            list: Status for each entity
        """
        entities = SyncMetadataService.get_all()
        return [SyncMetadataService.get_sync_status(e.entity) for e in entities]
    
    # ============================================================
    # WRITE / UPDATE
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def update_sync_time(
        entity: str,
        count: int,
        user: Optional[str] = None,
        request=None,
    ) -> SyncMetadata:
        """
        Update sync time for an entity after successful sync.
        
        Args:
            entity: Entity name
            count: Number of records synced
            user: User performing the action
            request: HTTP request object
        
        Returns:
            SyncMetadata: Updated instance
        
        Raises:
            ValidationError: If entity not found
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        if not metadata:
            raise ValidationError({'entity': f'Entity "{entity}" not found.'})
        
        metadata.mark_completed(count)
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_metadata_updated',
                model_name='SyncMetadata',
                object_id=str(metadata.id),
                changes={
                    'entity': entity,
                    'count': count,
                    'total_synced': metadata.total_synced,
                },
            )
        
        logger.info(f"[SyncMetadata] Updated sync time for {entity}: +{count} records")
        return metadata
    
    @staticmethod
    @transaction.atomic
    def update_status(
        entity: str,
        status: str,
        error_message: Optional[str] = None,
        user: Optional[str] = None,
        request=None,
    ) -> SyncMetadata:
        """
        Update sync status for an entity.
        
        Args:
            entity: Entity name
            status: 'idle', 'syncing', 'completed', 'failed'
            error_message: Optional error message (for failed status)
            user: User performing the action
            request: HTTP request object
        
        Returns:
            SyncMetadata: Updated instance
        
        Raises:
            ValidationError: If entity not found or invalid status
        """
        valid_statuses = [
            SyncMetadata.Status.IDLE,
            SyncMetadata.Status.SYNCING,
            SyncMetadata.Status.COMPLETED,
            SyncMetadata.Status.FAILED,
        ]
        if status not in valid_statuses:
            raise ValidationError({
                'status': f'Invalid status: {status}. Must be one of {valid_statuses}.'
            })
        
        metadata = SyncMetadataService.get_by_entity(entity)
        if not metadata:
            raise ValidationError({'entity': f'Entity "{entity}" not found.'})
        
        if status == SyncMetadata.Status.SYNCING:
            metadata.mark_syncing()
        elif status == SyncMetadata.Status.COMPLETED:
            # Don't reset count here - use update_sync_time for that
            metadata.status = status
            metadata.error_message = None
            metadata.save(update_fields=['status', 'error_message', 'updated_at'])
        elif status == SyncMetadata.Status.FAILED:
            metadata.mark_failed(error_message or 'Unknown error')
        else:  # IDLE
            metadata.reset()
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_status_updated',
                model_name='SyncMetadata',
                object_id=str(metadata.id),
                changes={
                    'entity': entity,
                    'status': status,
                    'error_message': error_message,
                },
            )
        
        logger.info(f"[SyncMetadata] Updated status for {entity}: {status}")
        return metadata
    
    @staticmethod
    @transaction.atomic
    def log_error(entity: str, error_message: str, user: Optional[str] = None, request=None) -> SyncMetadata:
        """
        Log a sync error for an entity.
        
        Args:
            entity: Entity name
            error_message: Error description
            user: User performing the action
            request: HTTP request object
        
        Returns:
            SyncMetadata: Updated instance
        """
        return SyncMetadataService.update_status(
            entity=entity,
            status=SyncMetadata.Status.FAILED,
            error_message=error_message,
            user=user,
            request=request,
        )
    
    @staticmethod
    @transaction.atomic
    def reset_status(entity: str, user: Optional[str] = None, request=None) -> SyncMetadata:
        """
        Reset sync status for an entity to idle.
        
        Args:
            entity: Entity name
            user: User performing the action
            request: HTTP request object
        
        Returns:
            SyncMetadata: Updated instance
        """
        return SyncMetadataService.update_status(
            entity=entity,
            status=SyncMetadata.Status.IDLE,
            user=user,
            request=request,
        )
    
    @staticmethod
    @transaction.atomic
    def reset_all_statuses(user: Optional[str] = None, request=None) -> Dict[str, Any]:
        """
        Reset all sync statuses to idle.
        
        Args:
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: {'reset_count': int}
        """
        entities = SyncMetadata.objects.filter(deleted_at__isnull=True)
        count = entities.count()
        
        for metadata in entities:
            metadata.reset()
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_reset_all',
                model_name='SyncMetadata',
                object_id='all',
                changes={'reset_count': count},
            )
        
        logger.info(f"[SyncMetadata] Reset all {count} sync statuses")
        return {'reset_count': count}
    
    # ============================================================
    # UTILITY
    # ============================================================
    
    @staticmethod
    def has_pending(entity: str) -> bool:
        """
        Check if an entity has pending sync.
        
        Args:
            entity: Entity name
        
        Returns:
            bool: True if status is syncing or failed
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        if not metadata:
            return False
        return metadata.status in [
            SyncMetadata.Status.SYNCING,
            SyncMetadata.Status.FAILED,
        ]
    
    @staticmethod
    def is_entity_ready(entity: str) -> bool:
        """
        Check if an entity is ready for sync (idle or completed).
        
        Args:
            entity: Entity name
        
        Returns:
            bool: True if ready to sync
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        if not metadata:
            return True  # Not initialized, can sync
        return metadata.status in [
            SyncMetadata.Status.IDLE,
            SyncMetadata.Status.COMPLETED,
        ]
    
    @staticmethod
    def get_entities_needing_sync() -> List[str]:
        """
        Get entities that need sync (never synced or status is idle).
        
        Returns:
            list: Entity names
        """
        # Entities that are idle or have never been synced
        never_synced = SyncMetadata.objects.filter(
            last_synced_at__isnull=True,
            deleted_at__isnull=True,
        ).values_list('entity', flat=True)
        
        # Entities that are idle
        idle = SyncMetadata.objects.filter(
            status=SyncMetadata.Status.IDLE,
            deleted_at__isnull=True,
        ).values_list('entity', flat=True)
        
        # Combine and deduplicate
        return list(set(list(never_synced) + list(idle)))
    
    @staticmethod
    def get_sync_duration(entity: str) -> Optional[float]:
        """
        Get the duration of the last sync for an entity.
        
        Args:
            entity: Entity name
        
        Returns:
            float: Duration in seconds, or None
        """
        metadata = SyncMetadataService.get_by_entity(entity)
        if not metadata or not metadata.last_sync_started_at or not metadata.last_synced_at:
            return None
        
        duration = (metadata.last_synced_at - metadata.last_sync_started_at).total_seconds()
        return round(duration, 2)
    
    @staticmethod
    def format_status(metadata: SyncMetadata) -> Dict[str, Any]:
        """
        Format sync metadata for display.
        
        Args:
            metadata: SyncMetadata instance
        
        Returns:
            dict: Formatted status
        """
        return {
            'entity': metadata.entity,
            'status': metadata.status,
            'status_display': metadata.get_status_display(),
            'last_synced_at': metadata.last_synced_at.isoformat() if metadata.last_synced_at else None,
            'last_sync_count': metadata.last_sync_count,
            'total_synced': metadata.total_synced,
            'has_error': bool(metadata.error_message),
            'error_message': metadata.error_message,
            'last_sync_started_at': metadata.last_sync_started_at.isoformat() if metadata.last_sync_started_at else None,
            'created_at': metadata.created_at.isoformat(),
            'updated_at': metadata.updated_at.isoformat(),
        }