# controls/views/sync.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from sync.tasks import (
    sync_health_check,
    force_sync_health_check,
    auto_retry_failed_queue_items,
    force_queue_retry,
    cleanup_stale_sync_metadata,
    generate_sync_report,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerSyncHealthCheckView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger sync health check",
        description="Run a health check on the sync system to detect issues.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SyncHealthCheckResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            task = force_sync_health_check.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_sync_health_check',
                model_name='Controls',
                object_id='sync_health',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Sync health check task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger sync health check")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerQueueRetryView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Retry failed queue items",
        description="Automatically retry failed sync queue items that are eligible for retry.",
        request=inline_serializer(
            name="QueueRetryRequest",
            fields={
                "entity": serializers.CharField(
                    required=False,
                    help_text="Filter by entity name (optional)"
                ),
                "limit": serializers.IntegerField(
                    default=50,
                    help_text="Maximum items to retry"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="QueueRetryResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            entity = request.data.get('entity')
            limit = request.data.get('limit', 50)
            task = force_queue_retry.delay(entity=entity, limit=limit)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_queue_retry',
                model_name='Controls',
                object_id='queue_retry',
                changes={'task_id': task.id, 'entity': entity, 'limit': limit},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Queue retry task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger queue retry")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSyncCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Cleanup stale sync data",
        description="Delete old sync metadata, queue items, conflicts, and task records.",
        request=inline_serializer(
            name="SyncCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=90,
                    help_text="Age in days for records to delete"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SyncCleanupResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            days = request.data.get('days', 90)
            task = cleanup_stale_sync_metadata.delay(days=days)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_sync_cleanup',
                model_name='Controls',
                object_id='sync_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Sync cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger sync cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSyncReportView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Generate sync report",
        description="Generate and send a sync activity report for the last N days.",
        request=inline_serializer(
            name="SyncReportRequest",
            fields={
                "days": serializers.IntegerField(
                    default=7,
                    help_text="Number of days for the report"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SyncReportResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskTriggerResponseSerializer(),
                    }
                ),
                description="Task queued successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def post(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to trigger tasks.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            days = request.data.get('days', 7)
            task = generate_sync_report.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_sync_report',
                model_name='Controls',
                object_id='sync_report',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Sync report generation task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger sync report")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )