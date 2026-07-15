# sync/views/sync.py
import logging
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone

from sync.services.sync import SyncService
from sync.services.sync_metadata import SyncMetadataService
from sync.services.sync_conflict import SyncConflictService
from sync.services.sync_queue import SyncQueueService
from sync.models.sync_metadata import SyncMetadata
from sync.models.sync_conflict import SyncConflict
from sync.models.sync_queue import SyncQueue

from users.permissions.base import IsAccountActive, can_edit, can_read
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)

logger = logging.getLogger(__name__)


# ============================================================
# SERIALIZERS
# ============================================================

class SyncMetadataSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = SyncMetadata
        fields = [
            'id', 'entity', 'last_synced_at', 'last_sync_count',
            'total_synced', 'status', 'status_display', 'error_message',
            'last_sync_started_at', 'created_at', 'updated_at'
        ]


class SyncConflictSerializer(serializers.ModelSerializer):
    resolution_display = serializers.CharField(source='get_resolution_display', read_only=True)
    is_pending = serializers.BooleanField(read_only=True)
    is_resolved = serializers.BooleanField(read_only=True)

    class Meta:
        model = SyncConflict
        fields = [
            'id', 'entity', 'entity_id', 'local_data', 'server_data',
            'merged_data', 'resolution', 'resolution_display', 'resolved_by',
            'resolved_at', 'local_updated_at', 'server_updated_at', 'notes',
            'is_pending', 'is_resolved', 'created_at', 'updated_at'
        ]


class SyncQueueSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = SyncQueue
        fields = [
            'id', 'entity', 'entity_id', 'action', 'action_display',
            'data', 'status', 'status_display', 'retry_count',
            'max_retries', 'error_message', 'processed_at',
            'created_at', 'updated_at'
        ]


class SyncStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = serializers.DictField()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ============================================================
# SYNC VIEW - Receive data from clients
# ============================================================

class SyncView(APIView):
    """
    Receive sync data from clients (pull sync).
    
    This is the main endpoint that offline clients use to push their data
    to the server. The server processes the data, detects conflicts,
    and returns the results.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Receive sync data from client",
        description="Process incoming sync data from offline clients for a specific entity.",
        parameters=[
            OpenApiParameter(
                name="entity_name",
                type=str,
                location=OpenApiParameter.PATH,
                description="Entity name (e.g., 'Borrower', 'Debt')",
                required=True,
            ),
        ],
        request=inline_serializer(
            name="SyncRequest",
            fields={
                "data": serializers.ListField(
                    child=serializers.DictField(),
                    help_text="List of records to sync"
                ),
                "user": serializers.CharField(
                    required=False,
                    default="system",
                    help_text="Client user identifier"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="SyncResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                name="Success Response",
                value={
                    "status": True,
                    "message": "Synced 5 records for Borrower",
                    "data": {
                        "entity": "Borrower",
                        "total": 5,
                        "created": 2,
                        "updated": 3,
                        "skipped": 0,
                        "errors": [],
                        "conflicts": [],
                        "ids": [1, 2, 3, 4, 5]
                    }
                },
                status_codes=["200"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request, entity_name):
        """Process sync data from client."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to sync data."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data.get('data', [])
        client_user = request.data.get('user', user.username)

        if not data:
            return _error(
                data={"detail": "No data to sync."},
                message="No data provided.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = SyncService.pull_sync(
                entity_name=entity_name,
                records=data,
                client_user=client_user,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_receive',
                model_name=entity_name,
                object_id='sync',
                changes={
                    'total': result['total'],
                    'created': result['created'],
                    'updated': result['updated'],
                    'conflicts': len(result['conflicts']),
                    'errors': len(result['errors']),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Synced {len(data)} records for {entity_name}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Sync failed for {entity_name}: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Sync failed.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC STATUS VIEW
# ============================================================

class SyncStatusView(APIView):
    """
    Get sync status for all entities or a specific entity.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Get sync status",
        description="Get sync status for all entities or filter by entity name.",
        parameters=[
            OpenApiParameter(
                name="entity",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by entity name",
                required=False,
            ),
        ],
        responses={
            200: SyncStatusResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        """Get sync status."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view sync status."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.query_params.get('entity')

            if entity:
                status_data = SyncService.get_status(entity)
                return _success(
                    data=status_data,
                    message=f"Sync status for {entity} retrieved.",
                    status=status.HTTP_200_OK,
                )

            status_data = SyncService.get_status()
            return _success(
                data=status_data,
                message="Sync status retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Sync status error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to get sync status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC ENTITY VIEW
# ============================================================

class SyncEntityView(APIView):
    """
    Sync a specific entity (trigger sync).
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Sync specific entity",
        description="Trigger sync for a specific entity.",
        parameters=[
            OpenApiParameter(
                name="entity_name",
                type=str,
                location=OpenApiParameter.PATH,
                description="Entity name (e.g., 'Borrower')",
                required=True,
            ),
        ],
        request=inline_serializer(
            name="SyncEntityRequest",
            fields={
                "force": serializers.BooleanField(
                    required=False,
                    default=False,
                    help_text="Force sync even if no changes"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="SyncEntityResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request, entity_name):
        """Sync a specific entity."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to sync data."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = SyncService.sync_entity(
                entity_name=entity_name,
                user=user.username,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_trigger',
                model_name=entity_name,
                object_id='sync',
                changes={
                    'entity': entity_name,
                    'processed': result.get('processed', 0),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Sync triggered for {entity_name}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Sync entity error for {entity_name}: {exc}")
            return _error(
                data={"detail": str(exc)},
                message=f"Failed to sync {entity_name}.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC CONFLICTS VIEW
# ============================================================

class SyncConflictsView(APIView):
    """
    Get and manage sync conflicts.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Get conflicts",
        description="Get sync conflicts with optional filters.",
        parameters=[
            OpenApiParameter(
                name="entity",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by entity",
                required=False,
            ),
            OpenApiParameter(
                name="entity_id",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Filter by entity ID",
                required=False,
            ),
            OpenApiParameter(
                name="resolution",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by resolution (pending, local, server, manual, merged)",
                required=False,
                default="pending",
            ),
        ],
        responses={
            200: inline_serializer(
                name="ConflictsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        """Get conflicts."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view conflicts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.query_params.get('entity')
            entity_id = request.query_params.get('entity_id')
            resolution = request.query_params.get('resolution', 'pending')

            conflicts = SyncService.get_conflicts(
                entity=entity,
                entity_id=entity_id,
                resolution=resolution,
            )

            stats = SyncConflictService.get_statistics(entity)

            return _success(
                data={
                    'conflicts': conflicts,
                    'stats': stats,
                    'total': len(conflicts),
                },
                message="Conflicts retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Conflicts error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to get conflicts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Sync"],
        summary="Resolve conflict",
        description="Resolve a specific sync conflict.",
        parameters=[
            OpenApiParameter(
                name="conflict_id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Conflict ID",
                required=True,
            ),
        ],
        request=inline_serializer(
            name="ResolveConflictRequest",
            fields={
                "resolution": serializers.ChoiceField(
                    choices=['local', 'server', 'manual', 'merged'],
                    help_text="Resolution type"
                ),
                "merged_data": serializers.DictField(
                    required=False,
                    allow_null=True,
                    help_text="Merged data (required for 'merged' resolution)"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="ResolveConflictResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": SyncConflictSerializer(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request, conflict_id):
        """Resolve a conflict."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to resolve conflicts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        resolution = request.data.get('resolution')
        merged_data = request.data.get('merged_data')

        if not resolution:
            return _error(
                data={"detail": "resolution is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if resolution == 'merged' and not merged_data:
            return _error(
                data={"detail": "merged_data is required for 'merged' resolution."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = SyncService.resolve_conflict(
                conflict_id=conflict_id,
                resolution=resolution,
                resolved_by=user.username,
                merged_data=merged_data,
                user=user.username,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_resolve_conflict',
                model_name='SyncConflict',
                object_id=str(conflict_id),
                changes={'resolution': resolution},
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Conflict resolved with {resolution}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Resolve conflict error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to resolve conflict.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC AUTO-RESOLVE VIEW
# ============================================================

class SyncAutoResolveView(APIView):
    """
    Auto-resolve all pending conflicts.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Auto-resolve conflicts",
        description="Auto-resolve all pending conflicts using Last Write Wins (server priority).",
        request=inline_serializer(
            name="AutoResolveRequest",
            fields={
                "entity": serializers.CharField(
                    required=False,
                    allow_null=True,
                    help_text="Filter by entity"
                ),
                "entity_id": serializers.IntegerField(
                    required=False,
                    allow_null=True,
                    help_text="Filter by entity ID"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="AutoResolveResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request):
        """Auto-resolve conflicts."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to auto-resolve conflicts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.data.get('entity')
            entity_id = request.data.get('entity_id')

            result = SyncService.auto_resolve_all(
                entity=entity,
                user=user.username,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_auto_resolve',
                model_name='SyncConflict',
                object_id='all',
                changes={'resolved': result['resolved']},
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Auto-resolved {result['resolved']} conflicts",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Auto-resolve error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to auto-resolve conflicts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC QUEUE VIEW
# ============================================================

class SyncQueueView(APIView):
    """
    Manage sync queue.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Get queue status",
        description="Get sync queue status and pending items.",
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Maximum items to return",
                required=False,
                default=50,
            ),
        ],
        responses={
            200: inline_serializer(
                name="QueueResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        """Get queue status."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view queue."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.query_params.get('limit', 50))
            status_data = SyncService.get_queue_status()

            return _success(
                data=status_data,
                message="Queue status retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Queue error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to get queue status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Sync"],
        summary="Enqueue item",
        description="Enqueue a record for sync.",
        request=inline_serializer(
            name="EnqueueRequest",
            fields={
                "entity": serializers.CharField(help_text="Entity name"),
                "entity_id": serializers.IntegerField(help_text="Record ID"),
                "action": serializers.ChoiceField(
                    choices=['create', 'update', 'delete'],
                    help_text="Action to perform"
                ),
                "data": serializers.DictField(
                    required=False,
                    allow_null=True,
                    help_text="Record data"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="EnqueueResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": SyncQueueSerializer(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request):
        """Enqueue a record for sync."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to enqueue items."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        entity = request.data.get('entity')
        entity_id = request.data.get('entity_id')
        action = request.data.get('action')
        data = request.data.get('data')

        if not entity or not entity_id or not action:
            return _error(
                data={"detail": "entity, entity_id, and action are required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = SyncService.enqueue(
                entity=entity,
                entity_id=entity_id,
                action=action,
                data=data,
                user=user.username,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_enqueue',
                model_name='SyncQueue',
                object_id=str(result['id']),
                changes={'entity': entity, 'entity_id': entity_id, 'action': action},
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Enqueued {action} for {entity}#{entity_id}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Enqueue error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to enqueue item.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC PROCESS QUEUE VIEW
# ============================================================

class SyncProcessQueueView(APIView):
    """
    Process pending queue items.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Process queue",
        description="Process pending queue items.",
        request=inline_serializer(
            name="ProcessQueueRequest",
            fields={
                "limit": serializers.IntegerField(
                    required=False,
                    default=50,
                    help_text="Maximum items to process"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="ProcessQueueResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request):
        """Process pending queue items."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to process queue."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.data.get('limit', 50))
            result = SyncService.process_queue(
                limit=limit,
                user=user.username,
                request=request,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_process_queue',
                model_name='SyncQueue',
                object_id='batch',
                changes={
                    'processed': result['processed'],
                    'completed': result['completed'],
                    'failed': result['failed'],
                },
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Processed {result['processed']} queue items",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Process queue error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to process queue.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC CLEANUP VIEW
# ============================================================

class SyncCleanupView(APIView):
    """
    Cleanup old sync data.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Cleanup sync data",
        description="Delete old sync data (resolved conflicts and completed queue items).",
        request=inline_serializer(
            name="CleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    required=False,
                    default=30,
                    help_text="Age in days"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="CleanupResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request):
        """Cleanup old sync data."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to cleanup sync data."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            days = int(request.data.get('days', 30))
            result = SyncService.cleanup(days)

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_cleanup',
                model_name='Sync',
                object_id='cleanup',
                changes={
                    'days': days,
                    'queue_deleted': result['queue_items_deleted'],
                    'conflicts_deleted': result['conflicts_deleted'],
                },
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Cleanup completed (days: {days})",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Cleanup error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to cleanup sync data.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC RESET VIEW
# ============================================================

class SyncResetView(APIView):
    """
    Reset sync state.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Reset sync state",
        description="Reset sync state for all entities or a specific entity.",
        request=inline_serializer(
            name="ResetRequest",
            fields={
                "entity": serializers.CharField(
                    required=False,
                    allow_null=True,
                    help_text="Entity name (optional)"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="ResetResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    @transaction.atomic
    def post(self, request):
        """Reset sync state."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to reset sync state."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.data.get('entity')
            result = SyncService.reset_sync_state(entity)

            log_audit_event(
                request=request,
                user=user,
                action_type='sync_reset',
                model_name='Sync',
                object_id='reset',
                changes={'entity': entity or 'all'},
                ip_address=client_ip,
            )

            return _success(
                data=result,
                message=f"Sync state reset for {entity or 'all entities'}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Reset error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to reset sync state.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC HEALTH VIEW
# ============================================================

class SyncHealthView(APIView):
    """
    Get sync system health.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Get sync health",
        description="Get health status of the sync system.",
        responses={
            200: inline_serializer(
                name="HealthResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        """Get sync health."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view sync health."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            health = SyncService.get_health()
            return _success(
                data=health,
                message="Sync health retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Health error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to get sync health.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# SYNC TEST VIEW (Debug)
# ============================================================

class SyncTestView(APIView):
    """
    Test sync for a specific entity (debug).
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Sync"],
        summary="Test sync (debug)",
        description="Test sync for a specific entity. Returns sample data.",
        parameters=[
            OpenApiParameter(
                name="entity",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Entity name",
                required=False,
                default="Borrower",
            ),
        ],
        responses={
            200: inline_serializer(
                name="TestResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        """Test sync for a specific entity."""
        user = request.user
        client_ip = get_client_ip(request)

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to test sync."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.query_params.get('entity', 'Borrower')
            result = SyncService.test_sync(entity)

            if 'error' in result:
                return _error(
                    data={"detail": result['error']},
                    message="Test failed.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return _success(
                data=result,
                message=f"Test completed for {entity}",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(f"Test error: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to test sync.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )