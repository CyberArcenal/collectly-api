# sync/services/sync_conflict.py
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Count, Sum

from sync.models.sync_conflict import SyncConflict
from sync.models.sync_metadata import SyncMetadata
from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


class SyncConflictService:
    """
    Service for managing sync conflicts between local and server data.
    
    Handles:
    - Creating conflicts
    - Retrieving conflicts with filters
    - Resolving conflicts (auto/manual)
    - Conflict statistics
    """
    
    # ============================================================
    # CREATE
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def create_conflict(
        entity: str,
        entity_id: int,
        local_data: Dict[str, Any],
        server_data: Dict[str, Any],
        local_updated_at: Optional[datetime] = None,
        server_updated_at: Optional[datetime] = None,
        notes: Optional[str] = None,
        user: Optional[str] = None,
        request=None,
    ) -> SyncConflict:
        """
        Create a new conflict record.
        
        Args:
            entity: Entity name (e.g., 'Borrower', 'Debt')
            entity_id: ID of the record with conflict
            local_data: Local version of the record
            server_data: Server version of the record
            local_updated_at: Local record's updated_at timestamp
            server_updated_at: Server record's updated_at timestamp
            notes: Additional notes
            user: User creating the conflict
            request: HTTP request object for audit
        
        Returns:
            SyncConflict: The created conflict instance
        
        Raises:
            ValidationError: If validation fails
        """
        # Check if there's already a pending conflict for this entity/id
        existing = SyncConflict.objects.filter(
            entity=entity,
            entity_id=entity_id,
            resolution=SyncConflict.Resolution.PENDING,
        ).first()
        
        if existing:
            # Update existing conflict with new data
            existing.local_data = local_data
            existing.server_data = server_data
            existing.local_updated_at = local_updated_at
            existing.server_updated_at = server_updated_at
            existing.notes = notes or existing.notes
            existing.updated_at = timezone.now()
            existing.save()
            
            logger.debug(f"[SyncConflict] Updated existing conflict for {entity}#{entity_id}")
            
            # Audit log
            if user and request:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type='sync_conflict_updated',
                    model_name='SyncConflict',
                    object_id=str(existing.id),
                    changes={'entity': entity, 'entity_id': entity_id},
                )
            
            return existing
        
        # Create new conflict
        conflict = SyncConflict.objects.create(
            entity=entity,
            entity_id=entity_id,
            local_data=local_data,
            server_data=server_data,
            local_updated_at=local_updated_at,
            server_updated_at=server_updated_at,
            notes=notes,
        )
        
        logger.warning(f"[SyncConflict] Created conflict for {entity}#{entity_id}")
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_conflict_created',
                model_name='SyncConflict',
                object_id=str(conflict.id),
                changes={'entity': entity, 'entity_id': entity_id},
            )
        
        return conflict
    
    # ============================================================
    # READ / RETRIEVE
    # ============================================================
    
    @staticmethod
    def get_by_id(conflict_id: int) -> Optional[SyncConflict]:
        """
        Get a conflict by ID.
        
        Args:
            conflict_id: ID of the conflict
        
        Returns:
            SyncConflict instance or None if not found
        """
        try:
            return SyncConflict.objects.get(id=conflict_id)
        except SyncConflict.DoesNotExist:
            return None
    
    @staticmethod
    def get_by_entity(
        entity: str,
        entity_id: Optional[int] = None,
        resolution: Optional[str] = None,
        limit: int = 100,
    ) -> List[SyncConflict]:
        """
        Get conflicts for a specific entity.
        
        Args:
            entity: Entity name
            entity_id: Optional specific record ID
            resolution: Optional filter by resolution
            limit: Maximum number of results
        
        Returns:
            List of SyncConflict instances
        """
        qs = SyncConflict.objects.filter(entity=entity)
        
        if entity_id:
            qs = qs.filter(entity_id=entity_id)
        
        if resolution:
            qs = qs.filter(resolution=resolution)
        
        return qs.order_by('-created_at')[:limit]
    
    @staticmethod
    def get_pending(
        entity: Optional[str] = None,
        limit: int = 100,
    ) -> List[SyncConflict]:
        """
        Get pending conflicts.
        
        Args:
            entity: Optional filter by entity
            limit: Maximum number of results
        
        Returns:
            List of pending SyncConflict instances
        """
        qs = SyncConflict.objects.filter(
            resolution=SyncConflict.Resolution.PENDING
        )
        
        if entity:
            qs = qs.filter(entity=entity)
        
        return qs.order_by('created_at')[:limit]
    
    @staticmethod
    def count_pending(entity: Optional[str] = None) -> int:
        """
        Count pending conflicts.
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            Number of pending conflicts
        """
        qs = SyncConflict.objects.filter(
            resolution=SyncConflict.Resolution.PENDING
        )
        
        if entity:
            qs = qs.filter(entity=entity)
        
        return qs.count()
    
    @staticmethod
    def get_latest(entity: str, entity_id: int) -> Optional[SyncConflict]:
        """
        Get the most recent conflict for a specific entity/id.
        
        Args:
            entity: Entity name
            entity_id: Record ID
        
        Returns:
            SyncConflict instance or None
        """
        return SyncConflict.objects.filter(
            entity=entity,
            entity_id=entity_id,
        ).order_by('-created_at').first()
    
    @staticmethod
    def has_pending(entity: str, entity_id: int) -> bool:
        """
        Check if there's a pending conflict for an entity/id.
        
        Args:
            entity: Entity name
            entity_id: Record ID
        
        Returns:
            True if there's a pending conflict
        """
        return SyncConflict.objects.filter(
            entity=entity,
            entity_id=entity_id,
            resolution=SyncConflict.Resolution.PENDING,
        ).exists()
    
    # ============================================================
    # STATISTICS
    # ============================================================
    
    @staticmethod
    def get_statistics(entity: Optional[str] = None) -> Dict[str, Any]:
        """
        Get conflict statistics.
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            dict: Statistics including counts by resolution
        """
        qs = SyncConflict.objects.all()
        
        if entity:
            qs = qs.filter(entity=entity)
        
        total = qs.count()
        
        # Count by resolution
        by_resolution = qs.values('resolution').annotate(
            count=Count('id')
        ).order_by('resolution')
        
        resolution_stats = {}
        for item in by_resolution:
            resolution_stats[item['resolution']] = item['count']
        
        # Count pending by entity
        pending_by_entity = SyncConflict.objects.filter(
            resolution=SyncConflict.Resolution.PENDING
        ).values('entity').annotate(
            count=Count('id')
        ).order_by('-count')
        
        return {
            'total': total,
            'pending': resolution_stats.get('pending', 0),
            'resolved': total - resolution_stats.get('pending', 0),
            'by_resolution': resolution_stats,
            'pending_by_entity': [
                {'entity': item['entity'], 'count': item['count']}
                for item in pending_by_entity
            ],
        }
    
    @staticmethod
    def get_entity_summary() -> List[Dict[str, Any]]:
        """
        Get conflict summary by entity.
        
        Returns:
            list: Summary per entity with counts
        """
        results = SyncConflict.objects.values('entity').annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(resolution=SyncConflict.Resolution.PENDING)),
            resolved=Count('id', filter=~Q(resolution=SyncConflict.Resolution.PENDING)),
        ).order_by('-total')
        
        return [
            {
                'entity': item['entity'],
                'total': item['total'],
                'pending': item['pending'],
                'resolved': item['resolved'],
            }
            for item in results
        ]
    
    # ============================================================
    # RESOLUTION
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def resolve_conflict(
        conflict_id: int,
        resolution: str,
        resolved_by: str = 'system',
        merged_data: Optional[Dict[str, Any]] = None,
        user: Optional[str] = None,
        request=None,
    ) -> SyncConflict:
        """
        Resolve a conflict.
        
        Args:
            conflict_id: ID of the conflict
            resolution: 'local', 'server', 'manual', 'merged'
            resolved_by: User who resolved
            merged_data: Merged data (required for 'merged')
            user: User performing the action (for audit)
            request: HTTP request object (for audit)
        
        Returns:
            SyncConflict: The resolved conflict instance
        
        Raises:
            ValidationError: If invalid or already resolved
        """
        conflict = SyncConflictService.get_by_id(conflict_id)
        if not conflict:
            raise ValidationError({'conflict_id': f'Conflict {conflict_id} not found.'})
        
        if conflict.resolution != SyncConflict.Resolution.PENDING:
            raise ValidationError({
                'conflict_id': f'Conflict {conflict_id} is already resolved ({conflict.resolution}).'
            })
        
        valid_resolutions = [
            SyncConflict.Resolution.LOCAL,
            SyncConflict.Resolution.SERVER,
            SyncConflict.Resolution.MANUAL,
            SyncConflict.Resolution.MERGED,
        ]
        if resolution not in valid_resolutions:
            raise ValidationError({
                'resolution': f'Invalid resolution: {resolution}. Must be one of {valid_resolutions}.'
            })
        
        if resolution == SyncConflict.Resolution.MERGED and merged_data is None:
            raise ValidationError({
                'merged_data': 'Merged data is required for "merged" resolution.'
            })
        
        # Resolve the conflict
        conflict.resolve(resolution, resolved_by, merged_data)
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_conflict_resolved',
                model_name='SyncConflict',
                object_id=str(conflict.id),
                changes={
                    'resolution': resolution,
                    'resolved_by': resolved_by,
                },
            )
        
        logger.info(f"[SyncConflict] Resolved conflict {conflict_id} with {resolution} by {resolved_by}")
        return conflict
    
    @staticmethod
    @transaction.atomic
    def auto_resolve_for_entity(entity: str, entity_id: int) -> Dict[str, Any]:
        """
        Auto-resolve conflicts for a specific entity/id using Last Write Wins.
        
        Resolution rule: Whichever has newer updated_at timestamp wins.
        
        Args:
            entity: Entity name
            entity_id: Record ID
        
        Returns:
            dict: {'resolved': int, 'skipped': int}
        """
        conflicts = SyncConflict.objects.filter(
            entity=entity,
            entity_id=entity_id,
            resolution=SyncConflict.Resolution.PENDING,
        )
        
        resolved = 0
        for conflict in conflicts:
            local_time = conflict.local_updated_at
            server_time = conflict.server_updated_at
            
            # If both timestamps exist, use LWW
            if local_time and server_time:
                if server_time >= local_time:
                    resolution = SyncConflict.Resolution.SERVER
                else:
                    resolution = SyncConflict.Resolution.LOCAL
            else:
                # Default to server (safer)
                resolution = SyncConflict.Resolution.SERVER
            
            SyncConflictService.resolve_conflict(conflict.id, resolution, 'system')
            resolved += 1
        
        logger.info(f"[SyncConflict] Auto-resolved {resolved} conflicts for {entity}#{entity_id}")
        return {'resolved': resolved, 'skipped': 0}
    
    @staticmethod
    @transaction.atomic
    def auto_resolve_all(entity: Optional[str] = None) -> Dict[str, Any]:
        """
        Auto-resolve all pending conflicts using Last Write Wins.
        
        Args:
            entity: Optional filter by entity
        
        Returns:
            dict: {'resolved': int, 'failed': int}
        """
        qs = SyncConflict.objects.filter(
            resolution=SyncConflict.Resolution.PENDING
        )
        
        if entity:
            qs = qs.filter(entity=entity)
        
        total = qs.count()
        resolved = 0
        failed = 0
        
        for conflict in qs:
            try:
                local_time = conflict.local_updated_at
                server_time = conflict.server_updated_at
                
                if local_time and server_time:
                    if server_time >= local_time:
                        resolution = SyncConflict.Resolution.SERVER
                    else:
                        resolution = SyncConflict.Resolution.LOCAL
                else:
                    resolution = SyncConflict.Resolution.SERVER
                
                SyncConflictService.resolve_conflict(conflict.id, resolution, 'system')
                resolved += 1
                
            except Exception as e:
                failed += 1
                logger.error(f"[SyncConflict] Failed to auto-resolve conflict {conflict.id}: {e}")
        
        logger.info(f"[SyncConflict] Auto-resolved {resolved} conflicts ({failed} failed)")
        return {'resolved': resolved, 'failed': failed, 'total': total}
    
    # ============================================================
    # DELETE / CLEANUP
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def delete_conflict(conflict_id: int, user: Optional[str] = None, request=None) -> bool:
        """
        Delete a conflict record.
        
        Args:
            conflict_id: ID of the conflict
            user: User performing the action (for audit)
            request: HTTP request object (for audit)
        
        Returns:
            bool: True if deleted, False if not found
        """
        conflict = SyncConflictService.get_by_id(conflict_id)
        if not conflict:
            return False
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_conflict_deleted',
                model_name='SyncConflict',
                object_id=str(conflict.id),
                changes={'resolution': conflict.resolution},
            )
        
        conflict.delete()
        logger.debug(f"[SyncConflict] Deleted conflict {conflict_id}")
        return True
    
    @staticmethod
    @transaction.atomic
    def cleanup_resolved(days: int = 30) -> int:
        """
        Delete resolved conflicts older than given days.
        
        Args:
            days: Age in days
        
        Returns:
            int: Number of conflicts deleted
        """
        cutoff = timezone.now() - timezone.timedelta(days=days)
        
        deleted, _ = SyncConflict.objects.filter(
            resolved_at__lt=cutoff,
            resolved_at__isnull=False,
        ).exclude(
            resolution=SyncConflict.Resolution.PENDING,
        ).delete()
        
        logger.info(f"[SyncConflict] Cleaned up {deleted} old resolved conflicts")
        return deleted
    
    # ============================================================
    # UTILITY
    # ============================================================
    
    @staticmethod
    def is_entity_has_conflicts(entity: str, entity_id: int) -> bool:
        """
        Check if a specific record has conflicts.
        
        Args:
            entity: Entity name
            entity_id: Record ID
        
        Returns:
            bool: True if there are pending conflicts
        """
        return SyncConflict.objects.filter(
            entity=entity,
            entity_id=entity_id,
            resolution=SyncConflict.Resolution.PENDING,
        ).exists()
    
    @staticmethod
    def get_conflicting_ids(entity: str) -> List[int]:
        """
        Get all IDs with pending conflicts for an entity.
        
        Args:
            entity: Entity name
        
        Returns:
            list: List of entity IDs with conflicts
        """
        return list(SyncConflict.objects.filter(
            entity=entity,
            resolution=SyncConflict.Resolution.PENDING,
        ).values_list('entity_id', flat=True).distinct())
    
    @staticmethod
    def get_conflict_details(conflict: SyncConflict) -> Dict[str, Any]:
        """
        Get human-readable conflict details.
        
        Args:
            conflict: SyncConflict instance
        
        Returns:
            dict: Human-readable conflict details
        """
        return {
            'id': conflict.id,
            'entity': conflict.entity,
            'entity_id': conflict.entity_id,
            'resolution': conflict.resolution,
            'resolved_by': conflict.resolved_by,
            'resolved_at': conflict.resolved_at,
            'local_updated_at': conflict.local_updated_at,
            'server_updated_at': conflict.server_updated_at,
            'notes': conflict.notes,
            'created_at': conflict.created_at,
            'local_fields': list(conflict.local_data.keys()) if conflict.local_data else [],
            'server_fields': list(conflict.server_data.keys()) if conflict.server_data else [],
            'is_resolved': conflict.is_resolved(),
            'is_pending': conflict.is_pending(),
        }