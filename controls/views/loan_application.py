# controls/views/loan_application.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from loan_applications.tasks import (
    force_auto_approve,
    force_cleanup_stale,
    force_pending_reminders,
    bulk_import_applications,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerAutoApproveView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger auto-approval",
        description="Automatically approve pending loan applications based on credit score thresholds.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="AutoApproveTriggerResponse",
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
            task = force_auto_approve.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_auto_approve',
                model_name='Controls',
                object_id='loan_application_auto_approve',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Auto-approval task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger auto-approval")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerStaleCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Clean up stale loan applications",
        description="Soft delete pending/rejected loan applications older than N days.",
        request=inline_serializer(
            name="StaleCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=30,
                    help_text="Age in days for applications to be considered stale"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="StaleCleanupTriggerResponse",
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
            days = request.data.get('days', 30)
            task = force_cleanup_stale.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_stale_cleanup',
                model_name='Controls',
                object_id='loan_application_stale_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Stale cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger stale cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerPendingRemindersView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Send pending application reminders",
        description="Send reminders for pending applications waiting for more than N days.",
        request=inline_serializer(
            name="PendingRemindersRequest",
            fields={
                "days": serializers.IntegerField(
                    default=7,
                    help_text="Days after which to send a reminder"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="PendingRemindersTriggerResponse",
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
            task = force_pending_reminders.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_pending_reminders',
                model_name='Controls',
                object_id='loan_application_pending_reminders',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Pending reminders task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger pending reminders")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerBulkImportApplicationsView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger bulk import of loan applications",
        description="Import loan applications from CSV file.",
        request=inline_serializer(
            name="BulkImportRequest",
            fields={
                "file_path": serializers.CharField(help_text="Path to CSV file"),
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="BulkImportTriggerResponse",
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
            file_path = request.data.get('file_path')
            if not file_path:
                return _error(
                    data={"detail": "file_path is required."},
                    message="Missing required parameter.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            task = bulk_import_applications.delay(file_path=file_path, user=user.username, request_data=request.data)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_bulk_import_applications',
                model_name='Controls',
                object_id='loan_application_bulk_import',
                changes={'task_id': task.id, 'file_path': file_path},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Bulk import task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger bulk import")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )