# controls/views/zero_balance.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from debts.tasks import force_zero_balance_fix, get_zero_balance_fixer_status
from controls.serializers import (
    TaskTriggerResponseSerializer,
    TaskStatusResponseSerializer,
    ErrorResponseSerializer,
)

logger = logging.getLogger(__name__)


class TriggerZeroBalanceFixerView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger zero balance fixer",
        description="Manually trigger the task that fixes debts with zero balance but incorrect status.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="ZeroBalanceFixerTriggerResponse",
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
            task = force_zero_balance_fix.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_zero_balance_fixer',
                model_name='Controls',
                object_id='zero_balance_fixer',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Zero balance fixer task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger zero balance fixer")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ZeroBalanceFixerStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get zero balance fixer status",
        description="Retrieve the current status and last run information for the zero balance fixer.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="ZeroBalanceFixerStatusResponse",
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
            stats = get_zero_balance_fixer_status()
            return _success(
                data=stats,
                message="Zero balance fixer status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get zero balance fixer status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )