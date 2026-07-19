import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from django.core.cache import cache
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from notifications.tasks.reminder import force_overdue_reminders
from notifications.models.notification_log import NotificationLog

from controls.serializers import (
    TaskTriggerResponseSerializer,
    TaskStatusResponseSerializer,
    ErrorResponseSerializer,
)

logger = logging.getLogger(__name__)


class TriggerOverdueRemindersView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger overdue reminders",
        description="Manually trigger the task that sends overdue reminder emails to borrowers.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueRemindersTriggerResponse",
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
            task = force_overdue_reminders.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_overdue_reminders',
                model_name='Controls',
                object_id='overdue_reminders',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Overdue reminders task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger overdue reminders")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OverdueRemindersStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get overdue reminders status",
        description="Retrieve the last run information for the overdue reminder task.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueRemindersStatusResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": TaskStatusResponseSerializer(),
                    }
                ),
                description="Status retrieved successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def get(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to view task status.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            LAST_RUN_KEY = "overdue_reminder_last_run"
            last_run = cache.get(LAST_RUN_KEY)
            stats = {
                'enabled': True,
                'last_run': last_run,
                'is_running': False,
                'schedule': 'Daily at 9:00 AM',
            }
            return _success(
                data=stats,
                message="Overdue reminders status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get overdue reminders status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerNotificationRetryView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Retry failed notifications",
        description="Manually trigger the task that retries failed email/SMS notifications.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="NotificationRetryTriggerResponse",
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
            from notifications.tasks.reminder import retry_failed_notifications
            task = retry_failed_notifications.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_notification_retry',
                model_name='Controls',
                object_id='notification_retry',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Notification retry task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger notification retry")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class NotificationRetryStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get notification retry status",
        description="Retrieve information about failed notifications pending retry.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="NotificationRetryStatusResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": serializers.DictField(),
                    }
                ),
                description="Status retrieved successfully.",
            ),
            403: OpenApiResponse(response=ErrorResponseSerializer, description="Permission denied"),
            500: OpenApiResponse(response=ErrorResponseSerializer, description="Server error"),
        },
    )
    def get(self, request):
        user = request.user
        if not can_edit(user):
            return _error(
                data={"detail": "Permission denied."},
                message="You do not have permission to view task status.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            failed_count = NotificationLog.objects.filter(
                status=NotificationLog.Status.FAILED,
                retry_count__lt=3,
            ).count()

            data = {
                'failed_count': failed_count,
                'max_retries': 3,
                'status': 'active',
            }
            return _success(
                data=data,
                message="Notification retry status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get notification retry status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )