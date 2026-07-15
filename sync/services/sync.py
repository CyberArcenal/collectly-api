# sync/services/sync.py
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q, Count, Sum, F, Value
from django.db.models.functions import Coalesce

from sync.services.sync_metadata import SyncMetadataService
from sync.services.sync_conflict import SyncConflictService
from sync.services.sync_queue import SyncQueueService
from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_conflict import SyncConflict
from sync.models.sync_queue import SyncQueue

from borrowers.models.borrower import Borrower
from debts.models.debt import Debt
from payments.models.payment_transaction import PaymentTransaction
from payments.models.penalty_transaction import PenaltyTransaction
from loan_agreements.models.loan_agreement import LoanAgreement
from loan_applications.models.loan_application import LoanApplication
from payment_methods.models.payment_method import PaymentMethod

from audit.utils.log import log_audit_event

logger = logging.getLogger(__name__)


# ============================================================
# ENTITY CONFIGURATION
# ============================================================

ENTITY_CONFIG = {
    'Borrower': {
        'model': Borrower,
        'fields': [
            'id', 'name', 'contact', 'email', 'address', 'notes',
            'deleted_at', 'created_at', 'updated_at'
        ],
        'id_field': 'id',
        'display_name': 'Borrower',
    },
    'Debt': {
        'model': Debt,
        'fields': [
            'id', 'name', 'total_amount', 'paid_amount', 'remaining_amount',
            'due_date', 'status', 'interest_rate', 'penalty_rate',
            'borrower_id', 'deleted_at', 'created_at', 'updated_at'
        ],
        'id_field': 'id',
        'display_name': 'Debt',
    },
    'PaymentTransaction': {
        'model': PaymentTransaction,
        'fields': [
            'id', 'amount', 'payment_date', 'reference', 'notes',
            'method_id', 'debt_id', 'deleted_at', 'recorded_at'
        ],
        'id_field': 'id',
        'display_name': 'Payment Transaction',
    },
    'PenaltyTransaction': {
        'model': PenaltyTransaction,
        'fields': [
            'id', 'amount', 'penalty_date', 'reason',
            'debt_id', 'deleted_at', 'created_at'
        ],
        'id_field': 'id',
        'display_name': 'Penalty Transaction',
    },
    'LoanAgreement': {
        'model': LoanAgreement,
        'fields': [
            'id', 'status', 'agreement_date', 'lender_name', 'terms_text',
            'file_path', 'debt_id', 'deleted_at', 'signed_at', 'signed_by',
            'principal_amount', 'interest_rate', 'penalty_rate',
            'due_date', 'purpose', 'loan_start_date', 'anniversary_day'
        ],
        'id_field': 'id',
        'display_name': 'Loan Agreement',
    },
    'LoanApplication': {
        'model': LoanApplication,
        'fields': [
            'id', 'debtor_id', 'debtor_name', 'debtor_contact',
            'debtor_email', 'debtor_address', 'requested_amount',
            'purpose', 'proposed_due_date', 'interest_rate',
            'status', 'approved_at', 'rejected_at', 'approved_by',
            'rejection_reason', 'deleted_at', 'created_at', 'updated_at'
        ],
        'id_field': 'id',
        'display_name': 'Loan Application',
    },
    'PaymentMethod': {
        'model': PaymentMethod,
        'fields': [
            'id', 'name', 'description', 'icon',
            'is_default', 'created_at', 'updated_at'
        ],
        'id_field': 'id',
        'display_name': 'Payment Method',
    },
}


class SyncService:
    """
    Main sync service orchestrating all sync operations.
    
    Handles:
    - Full sync (all entities)
    - Incremental sync (queue-based)
    - Entity-specific sync
    - Pull sync (receiving from clients)
    - Push sync (sending to clients)
    - Conflict detection and resolution
    - Sync status and monitoring
    """
    
    # ============================================================
    # INITIALIZATION
    # ============================================================
    
    @staticmethod
    def initialize():
        """
        Initialize the sync system.
        Creates sync metadata for all entities.
        """
        result = SyncMetadataService.initialize_entities()
        logger.info(f"[Sync] System initialized: {result['created']} entities created")
        return result
    
    # ============================================================
    # PULL SYNC (Receive from client)
    # ============================================================
    
    @staticmethod
    @transaction.atomic
    def pull_sync(
        entity_name: str,
        records: List[Dict[str, Any]],
        client_user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Receive and process sync data from a client (pull).
        
        This is the main entry point for client sync requests.
        
        Args:
            entity_name: Entity name (e.g., 'Borrower')
            records: List of records from client
            client_user: Client user identifier
            request: HTTP request object for audit
        
        Returns:
            dict: Results of the sync
        """
        # Get entity configuration
        config = ENTITY_CONFIG.get(entity_name)
        if not config:
            raise ValidationError({'entity': f'Unknown entity: {entity_name}'})
        
        # Update metadata to syncing
        try:
            SyncMetadataService.update_status(
                entity=entity_name,
                status=SyncMetadata.Status.SYNCING,
                user=client_user,
                request=request,
            )
        except ValidationError:
            # Entity not initialized, initialize it
            SyncMetadataService.initialize_entities([entity_name])
            SyncMetadataService.update_status(
                entity=entity_name,
                status=SyncMetadata.Status.SYNCING,
                user=client_user,
                request=request,
            )
        
        results = {
            'entity': entity_name,
            'total': len(records),
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [],
            'conflicts': [],
            'ids': [],
        }
        
        model = config['model']
        fields = config['fields']
        id_field = config['id_field']
        
        for record in records:
            try:
                record_id = record.get(id_field)
                
                if not record_id:
                    # Create new record (client didn't specify ID)
                    instance = SyncService._create_record(
                        model, record, fields, client_user, request
                    )
                    results['created'] += 1
                    results['ids'].append(instance.pk)
                    continue
                
                # Check if record exists on server
                existing = model.objects.filter(id=record_id).first()
                
                if existing:
                    # Check if client record is newer
                    client_updated = record.get('updated_at')
                    client_time = SyncService._parse_datetime(client_updated) if client_updated else None
                    
                    if client_time and existing.updated_at and existing.updated_at >= client_time:
                        results['skipped'] += 1
                        results['ids'].append(existing.pk)
                        continue
                    
                    # Check for potential conflict
                    if client_time and existing.updated_at:
                        # If server is newer, check if client has changes
                        if existing.updated_at > client_time:
                            # Server is newer - check if client has any differences
                            has_changes = SyncService._record_has_changes(
                                existing, record, fields
                            )
                            if has_changes:
                                # Create conflict
                                conflict = SyncConflictService.create_conflict(
                                    entity=entity_name,
                                    entity_id=record_id,
                                    local_data=record,
                                    server_data=SyncService._model_to_dict(existing, fields),
                                    local_updated_at=client_time,
                                    server_updated_at=existing.updated_at,
                                    notes=f"Conflict during pull sync from client {client_user}",
                                    user=client_user,
                                    request=request,
                                )
                                results['conflicts'].append({
                                    'id': record_id,
                                    'conflict_id': conflict.id,
                                    'message': 'Conflict detected - manual resolution required',
                                })
                                continue
                    
                    # Update existing record
                    SyncService._update_record(
                        existing, record, fields, client_user, request
                    )
                    results['updated'] += 1
                    results['ids'].append(existing.pk)
                else:
                    # Client has ID but server doesn't - create it
                    instance = SyncService._create_record(
                        model, record, fields, client_user, request,
                        force_id=record_id
                    )
                    results['created'] += 1
                    results['ids'].append(instance.pk)
                    
            except Exception as e:
                logger.error(f"[Sync] Failed to sync record {record.get(id_field)}: {e}")
                results['errors'].append({
                    'record': record,
                    'error': str(e),
                })
        
        # Update metadata
        total_processed = results['created'] + results['updated']
        if results['errors']:
            error_msgs = [e['error'] for e in results['errors']][:3]
            SyncMetadataService.log_error(
                entity=entity_name,
                error_message=f"Partial sync: {len(results['errors'])} errors: {', '.join(error_msgs)}",
                user=client_user,
                request=request,
            )
        else:
            SyncMetadataService.update_sync_time(
                entity=entity_name,
                count=total_processed,
                user=client_user,
                request=request,
            )
        
        logger.info(f"[Sync] Pull sync completed for {entity_name}: "
                   f"{results['created']} created, {results['updated']} updated, "
                   f"{len(results['conflicts'])} conflicts")
        
        return results
    
    # ============================================================
    # PUSH SYNC (Send to client)
    # ============================================================
    
    @staticmethod
    def push_sync(
        entity_name: str,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Get records to push to a client (push sync).
        
        Args:
            entity_name: Entity name
            since: Only get records changed after this time
            limit: Maximum number of records
        
        Returns:
            dict: Records to push
        """
        config = ENTITY_CONFIG.get(entity_name)
        if not config:
            raise ValidationError({'entity': f'Unknown entity: {entity_name}'})
        
        model = config['model']
        fields = config['fields']
        
        # Build query
        qs = model.objects.all()
        
        if since:
            qs = qs.filter(
                Q(updated_at__gte=since) | Q(created_at__gte=since)
            )
        
        # Include soft-deleted
        qs = qs.order_by('updated_at')[:limit]
        
        records = []
        for instance in qs:
            records.append(SyncService._model_to_dict(instance, fields))
        
        return {
            'entity': entity_name,
            'count': len(records),
            'records': records,
        }
    
    # ============================================================
    # FULL SYNC
    # ============================================================
    
    @staticmethod
    def full_sync(
        entities: Optional[List[str]] = None,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Perform a full sync of all or specific entities.
        
        Args:
            entities: Optional list of entity names. If None, syncs all.
            user: User performing the sync
            request: HTTP request object
        
        Returns:
            dict: Sync results for all entities
        """
        if entities is None:
            entities = list(ENTITY_CONFIG.keys())
        
        results = {
            'total': len(entities),
            'completed': 0,
            'failed': 0,
            'errors': [],
            'entities': {},
        }
        
        for entity_name in entities:
            try:
                # Get all records (not just changes)
                config = ENTITY_CONFIG.get(entity_name)
                if not config:
                    results['failed'] += 1
                    results['errors'].append({
                        'entity': entity_name,
                        'error': f'Unknown entity: {entity_name}',
                    })
                    continue
                
                model = config['model']
                fields = config['fields']
                
                # Get all records
                records = model.objects.all()
                record_list = [SyncService._model_to_dict(r, fields) for r in records]
                
                # Process sync (this is push - for now we just return the data)
                results['entities'][entity_name] = {
                    'total': len(record_list),
                    'records': record_list[:100],  # Limit for response
                    'has_more': len(record_list) > 100,
                }
                results['completed'] += 1
                
            except Exception as e:
                results['failed'] += 1
                results['errors'].append({
                    'entity': entity_name,
                    'error': str(e),
                })
                logger.error(f"[Sync] Full sync failed for {entity_name}: {e}")
        
        logger.info(f"[Sync] Full sync completed: {results['completed']} succeeded, {results['failed']} failed")
        return results
    
    # ============================================================
    # ENTITY SYNC (Incremental)
    # ============================================================
    
    @staticmethod
    def sync_entity(
        entity_name: str,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Sync a specific entity (incremental, based on queue).
        
        Args:
            entity_name: Entity name
            user: User performing the sync
            request: HTTP request object
        
        Returns:
            dict: Sync results
        """
        # Check if entity has pending queue items
        pending_count = SyncQueueService.count_pending(entity_name)
        
        if pending_count == 0:
            return {
                'entity': entity_name,
                'status': 'idle',
                'message': 'No pending items to sync',
                'processed': 0,
            }
        
        # Process queue items for this entity
        def handler(item):
            return SyncService._process_queue_item_impl(item)
        
        result = SyncQueueService.process_all(
            handler=handler,
            entity=entity_name,
            limit=50,
            user=user,
            request=request,
        )
        
        return {
            'entity': entity_name,
            'status': 'completed',
            'processed': result['processed'],
            'completed': result['completed'],
            'failed': result['failed'],
            'errors': result['errors'],
        }
    
    # ============================================================
    # QUEUE ITEM PROCESSING
    # ============================================================
    
    @staticmethod
    def _process_queue_item_impl(item: SyncQueue) -> Dict[str, Any]:
        """
        Implementation of queue item processing.
        
        Args:
            item: SyncQueue instance
        
        Returns:
            dict: {'success': bool, 'error': str}
        """
        config = ENTITY_CONFIG.get(item.entity)
        if not config:
            return {
                'success': False,
                'error': f'Unknown entity: {item.entity}',
            }
        
        model = config['model']
        fields = config['fields']
        
        try:
            if item.action == 'delete':
                # Delete the record
                instance = model.objects.filter(id=item.entity_id).first()
                if instance:
                    # Soft delete
                    if hasattr(instance, 'soft_delete'):
                        instance.soft_delete()
                    else:
                        instance.delete()
                return {'success': True}
            
            elif item.action in ['create', 'update']:
                if not item.data:
                    return {
                        'success': False,
                        'error': 'No data provided for create/update',
                    }
                
                # Check if record exists
                existing = model.objects.filter(id=item.entity_id).first()
                
                if existing:
                    # Update
                    SyncService._update_record(
                        existing, item.data, fields, 'system', None
                    )
                else:
                    # Create
                    SyncService._create_record(
                        model, item.data, fields, 'system', None,
                        force_id=item.entity_id
                    )
                
                return {'success': True}
            
            else:
                return {
                    'success': False,
                    'error': f'Unknown action: {item.action}',
                }
                
        except Exception as e:
            logger.error(f"[Sync] Queue item {item.id} processing failed: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    # ============================================================
    # CONFLICT MANAGEMENT
    # ============================================================
    
    @staticmethod
    def get_conflicts(
        entity: Optional[str] = None,
        entity_id: Optional[int] = None,
        resolution: str = 'pending',
    ) -> List[Dict[str, Any]]:
        """
        Get conflicts with optional filters.
        
        Args:
            entity: Optional entity name
            entity_id: Optional record ID
            resolution: Filter by resolution
        
        Returns:
            list: Formatted conflict details
        """
        conflicts = SyncConflictService.get_by_entity(
            entity=entity or '',
            entity_id=entity_id,
            resolution=resolution,
        )
        
        return [SyncConflictService.get_conflict_details(c) for c in conflicts]
    
    @staticmethod
    def resolve_conflict(
        conflict_id: int,
        resolution: str,
        resolved_by: str = 'system',
        merged_data: Optional[Dict[str, Any]] = None,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Resolve a conflict.
        
        Args:
            conflict_id: ID of the conflict
            resolution: 'local', 'server', 'manual', 'merged'
            resolved_by: User resolving
            merged_data: Merged data (for 'merged' resolution)
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: Resolution result
        """
        conflict = SyncConflictService.resolve_conflict(
            conflict_id=conflict_id,
            resolution=resolution,
            resolved_by=resolved_by,
            merged_data=merged_data,
            user=user,
            request=request,
        )
        
        return {
            'conflict_id': conflict.id,
            'resolution': conflict.resolution,
            'resolved_by': conflict.resolved_by,
            'resolved_at': conflict.resolved_at,
        }
    
    @staticmethod
    def auto_resolve_all(
        entity: Optional[str] = None,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Auto-resolve all pending conflicts.
        
        Args:
            entity: Optional filter by entity
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: Auto-resolution results
        """
        # Get pending conflicts
        conflicts = SyncConflictService.get_pending(entity)
        
        resolved = 0
        for conflict in conflicts:
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
                
                SyncConflictService.resolve_conflict(
                    conflict_id=conflict.id,
                    resolution=resolution,
                    resolved_by='system',
                    user=user,
                    request=request,
                )
                resolved += 1
            except Exception as e:
                logger.error(f"[Sync] Failed to auto-resolve conflict {conflict.id}: {e}")
        
        return {
            'total': len(conflicts),
            'resolved': resolved,
        }
    
    # ============================================================
    # QUEUE MANAGEMENT
    # ============================================================
    
    @staticmethod
    def enqueue(
        entity: str,
        entity_id: int,
        action: str,
        data: Optional[Dict[str, Any]] = None,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Enqueue a record for sync.
        
        Args:
            entity: Entity name
            entity_id: Record ID
            action: 'create', 'update', 'delete'
            data: Record data
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: Queue item details
        """
        item = SyncQueueService.enqueue(
            entity=entity,
            entity_id=entity_id,
            action=action,
            data=data,
            user=user,
            request=request,
        )
        
        return {
            'id': item.id,
            'entity': item.entity,
            'entity_id': item.entity_id,
            'action': item.action,
            'status': item.status,
        }
    
    @staticmethod
    def get_queue_status() -> Dict[str, Any]:
        """
        Get queue status.
        
        Returns:
            dict: Queue statistics and items
        """
        stats = SyncQueueService.get_statistics()
        pending = SyncQueueService.get_pending(10)
        
        return {
            'stats': stats,
            'pending_items': [SyncQueueService.format_item(item) for item in pending],
        }
    
    @staticmethod
    def process_queue(
        limit: int = 50,
        user: str = 'system',
        request=None,
    ) -> Dict[str, Any]:
        """
        Process pending queue items.
        
        Args:
            limit: Maximum items to process
            user: User performing the action
            request: HTTP request object
        
        Returns:
            dict: Processing results
        """
        result = SyncQueueService.process_all(
            handler=SyncService._process_queue_item_impl,
            limit=limit,
            user=user,
            request=request,
        )
        
        return result
    
    # ============================================================
    # STATUS AND MONITORING
    # ============================================================
    
    @staticmethod
    def get_status(entity: Optional[str] = None) -> Dict[str, Any]:
        """
        Get sync status.
        
        Args:
            entity: Optional entity name
        
        Returns:
            dict: Sync status
        """
        if entity:
            status = SyncMetadataService.get_sync_status(entity)
            queue_pending = SyncQueueService.count_pending(entity)
            has_conflicts = SyncConflictService.has_pending(entity, None) if entity else False
            
            return {
                'entity': status,
                'queue_pending': queue_pending,
                'has_conflicts': has_conflicts,
            }
        
        # Overall status
        metadata_summary = SyncMetadataService.get_summary()
        queue_stats = SyncQueueService.get_statistics()
        conflict_stats = SyncConflictService.get_statistics()
        
        return {
            'summary': metadata_summary,
            'queue': queue_stats,
            'conflicts': conflict_stats,
            'is_syncing': metadata_summary.get('is_syncing', False),
        }
    
    @staticmethod
    def get_health() -> Dict[str, Any]:
        """
        Get sync system health.
        
        Returns:
            dict: Health check results
        """
        # Check if metadata exists for all entities
        entities = list(ENTITY_CONFIG.keys())
        metadata = SyncMetadataService.get_all()
        metadata_entities = [m.entity for m in metadata]
        
        missing_entities = [e for e in entities if e not in metadata_entities]
        
        # Check for stuck items (processing > 1 hour)
        stuck = SyncQueue.objects.filter(
            status=SyncQueue.Status.PROCESSING,
            updated_at__lt=timezone.now() - timedelta(hours=1),
        ).count()
        
        # Check for high conflict count
        conflict_count = SyncConflictService.count_pending()
        
        return {
            'status': 'healthy' if not missing_entities and conflict_count < 10 else 'degraded',
            'missing_entities': missing_entities,
            'stuck_queue_items': stuck,
            'pending_conflicts': conflict_count,
            'entities': len(entities),
            'metadata_entities': len(metadata_entities),
        }
    
    # ============================================================
    # CLEANUP
    # ============================================================
    
    @staticmethod
    def cleanup(days: int = 30) -> Dict[str, Any]:
        """
        Cleanup old sync data.
        
        Args:
            days: Age in days
        
        Returns:
            dict: Cleanup results
        """
        queue_deleted = SyncQueueService.cleanup_completed(days)
        conflict_deleted = SyncConflictService.cleanup_resolved(days)
        
        return {
            'queue_items_deleted': queue_deleted,
            'conflicts_deleted': conflict_deleted,
            'days': days,
        }
    
    @staticmethod
    def reset_sync_state(entity: Optional[str] = None) -> Dict[str, Any]:
        """
        Reset sync state.
        
        Args:
            entity: Optional entity name. If None, resets all.
        
        Returns:
            dict: Reset results
        """
        if entity:
            SyncMetadataService.reset_status(entity)
            SyncQueueService.clear_entity(entity)
            return {'entity': entity, 'reset': True}
        
        # Reset all
        result = SyncMetadataService.reset_all_statuses()
        return {'reset': True, 'entities_reset': result.get('reset_count', 0)}
    
    # ============================================================
    # UTILITY HELPERS
    # ============================================================
    
    @staticmethod
    def _parse_datetime(value):
        """Parse datetime from ISO string."""
        if not value:
            return None
        try:
            if isinstance(value, datetime):
                return value
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def _model_to_dict(instance, fields):
        """Convert model instance to dict."""
        result = {}
        for field in fields:
            if hasattr(instance, field):
                val = getattr(instance, field)
                if isinstance(val, datetime):
                    val = val.isoformat()
                elif isinstance(val, Decimal):
                    val = float(val)
                elif isinstance(val, timezone):
                    val = val.isoformat()
                result[field] = val
        return result
    
    @staticmethod
    def _record_has_changes(instance, data, fields):
        """Check if record data has changes."""
        for field in fields:
            if field in data and field != 'id':
                current_val = getattr(instance, field, None)
                new_val = data.get(field)
                
                if isinstance(current_val, Decimal) and new_val is not None:
                    current_val = float(current_val)
                
                if str(current_val) != str(new_val):
                    return True
        return False
    
    @staticmethod
    @transaction.atomic
    def _create_record(model, data, fields, user=None, request=None, force_id=None):
        """Create a record from sync data."""
        clean_data = {k: v for k, v in data.items() if k in fields}
        clean_data.pop('id', None)
        
        # Handle date fields
        for field in ['created_at', 'updated_at', 'deleted_at']:
            if field in clean_data and clean_data[field]:
                clean_data[field] = SyncService._parse_datetime(clean_data[field])
        
        if force_id:
            clean_data['id'] = force_id
            instance = model(**clean_data)
        else:
            instance = model(**clean_data)
        
        instance.save()
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_create',
                model_name=model.__name__,
                object_id=str(instance.pk),
                changes={'sync': True},
            )
        
        return instance
    
    @staticmethod
    @transaction.atomic
    def _update_record(instance, data, fields, user=None, request=None):
        """Update a record from sync data."""
        clean_data = {k: v for k, v in data.items() if k in fields}
        clean_data.pop('id', None)
        clean_data.pop('created_at', None)
        
        # Handle date fields
        for field in ['updated_at', 'deleted_at']:
            if field in clean_data and clean_data[field]:
                clean_data[field] = SyncService._parse_datetime(clean_data[field])
        
        for key, value in clean_data.items():
            if value is not None:
                setattr(instance, key, value)
        
        instance.save()
        
        # Audit log
        if user and request:
            log_audit_event(
                request=request,
                user=user,
                action_type='sync_update',
                model_name=instance.__class__.__name__,
                object_id=str(instance.pk),
                changes={'sync': True},
            )
        
        return instance
    
    # ============================================================
    # TEST / DEBUG
    # ============================================================
    
    @staticmethod
    def test_sync(entity_name: str = 'Borrower') -> Dict[str, Any]:
        """
        Test sync for an entity (debug).
        
        Args:
            entity_name: Entity name
        
        Returns:
            dict: Test results
        """
        config = ENTITY_CONFIG.get(entity_name)
        if not config:
            return {'error': f'Unknown entity: {entity_name}'}
        
        model = config['model']
        fields = config['fields']
        
        # Get sample records
        records = model.objects.all()[:5]
        
        return {
            'entity': entity_name,
            'total_records': model.objects.count(),
            'sample_records': [SyncService._model_to_dict(r, fields) for r in records],
            'fields': fields,
        }