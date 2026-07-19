# controls/views/health.py
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
    check_overdue_status_health,
    check_zero_balance_health,
)
from payments.tasks.payment import check_penalty_application_health
from controls.serializers import (
    HealthCheckResponseSerializer,
    ErrorResponseSerializer,
)

logger = logging.getLogger(__name__)


class OverdueStatusHealthView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Run overdue status health check",
        description="Performs a health check to detect inconsistencies in debt overdue statuses.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueHealthResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": HealthCheckResponseSerializer(),
                    }
                ),
                description="Health check completed.",
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
                message="You do not have permission to view health.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = check_overdue_status_health()
            return _success(
                data=result,
                message="Overdue status health check completed.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to run overdue health check")
            return _error(
                data={"detail": str(e)},
                message="Failed to run health check.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ZeroBalanceHealthView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Run zero balance health check",
        description="Performs a health check to detect debts with zero balance that are not marked as paid, or paid debts with positive balance.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="ZeroBalanceHealthResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": HealthCheckResponseSerializer(),
                    }
                ),
                description="Health check completed.",
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
                message="You do not have permission to view health.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = check_zero_balance_health()
            return _success(
                data=result,
                message="Zero balance health check completed.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to run zero balance health check")
            return _error(
                data={"detail": str(e)},
                message="Failed to run health check.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PenaltyHealthView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Run penalty application health check",
        description="Performs a health check to detect debts missing penalties or paid debts with penalties.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PenaltyHealthResponse",
                    fields={
                        "status": serializers.BooleanField(),
                        "message": serializers.CharField(),
                        "data": HealthCheckResponseSerializer(),
                    }
                ),
                description="Health check completed.",
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
                message="You do not have permission to view health.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = check_penalty_application_health()
            return _success(
                data=result,
                message="Penalty application health check completed.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to run penalty health check")
            return _error(
                data={"detail": str(e)},
                message="Failed to run health check.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )