# controls/views/penalty.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from payments.tasks import force_penalty_application, get_penalty_scheduler_status
from controls.serializers import (
    TaskTriggerResponseSerializer,
    TaskStatusResponseSerializer,
    ErrorResponseSerializer,
)

logger = logging.getLogger(__name__)


class TriggerPenaltySchedulerView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger penalty scheduler",
        description="Manually trigger the task that applies auto-penalties to overdue debts.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="PenaltySchedulerTriggerResponse",
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
            task = force_penalty_application.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_penalty_scheduler',
                model_name='Controls',
                object_id='penalty_scheduler',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Penalty scheduler task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger penalty scheduler")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PenaltySchedulerStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get penalty scheduler status",
        description="Retrieve the current status and last run information for the penalty scheduler.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PenaltySchedulerStatusResponse",
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
            stats = get_penalty_scheduler_status()
            return _success(
                data=stats,
                message="Penalty scheduler status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get penalty scheduler status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )