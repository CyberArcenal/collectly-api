# controls/views/overdue.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from debts.tasks.debt import (
    force_overdue_correction,
    get_overdue_corrector_status,
    force_overdue_update,
    get_overdue_updater_status,
)
from controls.serializers import (
    TaskTriggerResponseSerializer,
    TaskStatusResponseSerializer,
    ErrorResponseSerializer,
)

logger = logging.getLogger(__name__)


# ---- Overdue Corrector ----

class TriggerOverdueCorrectorView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger overdue status corrector",
        description="Manually trigger the task that corrects misclassified overdue debts.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueCorrectorTriggerResponse",
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
            task = force_overdue_correction.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_overdue_corrector',
                model_name='Controls',
                object_id='overdue_corrector',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Overdue corrector task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger overdue corrector")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OverdueCorrectorStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get overdue corrector status",
        description="Retrieve the current status and last run information for the overdue status corrector.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueCorrectorStatusResponse",
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
            stats = get_overdue_corrector_status()
            return _success(
                data=stats,
                message="Overdue corrector status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get overdue corrector status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---- Overdue Updater ----

class TriggerOverdueUpdaterView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger overdue status updater",
        description="Manually trigger the task that updates active debts to overdue when due date has passed.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueUpdaterTriggerResponse",
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
            task = force_overdue_update.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_overdue_updater',
                model_name='Controls',
                object_id='overdue_updater',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Overdue updater task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger overdue updater")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OverdueUpdaterStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get overdue updater status",
        description="Retrieve the current status and last run information for the overdue status updater.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueUpdaterStatusResponse",
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
            stats = get_overdue_updater_status()
            return _success(
                data=stats,
                message="Overdue updater status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get overdue updater status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )