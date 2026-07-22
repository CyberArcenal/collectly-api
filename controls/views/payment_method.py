# controls/views/payment_method.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from payment_methods.tasks import (
    recalculate_payment_method_stats,
    force_payment_method_stats_recalc,
    cleanup_unused_payment_methods,
    generate_payment_method_report,
    ensure_default_payment_method_exists,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerPaymentMethodStatsRecalcView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Recalculate payment method stats",
        description="Recalculate transaction stats for all payment methods or specific ones.",
        request=inline_serializer(
            name="PaymentMethodStatsRecalcRequest",
            fields={
                "method_ids": serializers.ListField(
                    child=serializers.IntegerField(),
                    required=False,
                    help_text="Optional list of method IDs. If not provided, recalculates all."
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="PaymentMethodStatsRecalcResponse",
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
            method_ids = request.data.get('method_ids')
            task = force_payment_method_stats_recalc.delay(method_ids=method_ids, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_payment_method_stats_recalc',
                model_name='Controls',
                object_id='payment_method_stats',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Payment method stats recalculation task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger payment method stats recalculation")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerPaymentMethodCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Cleanup unused payment methods",
        description="Soft-delete payment methods with no transactions in N days.",
        request=inline_serializer(
            name="PaymentMethodCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=180,
                    help_text="Number of days without transactions to consider unused"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="PaymentMethodCleanupResponse",
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
            days = request.data.get('days', 180)
            task = cleanup_unused_payment_methods.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_payment_method_cleanup',
                model_name='Controls',
                object_id='payment_method_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Payment method cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger payment method cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerPaymentMethodReportView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Generate payment method usage report",
        description="Generate and send a usage report for all payment methods.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="PaymentMethodReportResponse",
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
            task = generate_payment_method_report.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_payment_method_report',
                model_name='Controls',
                object_id='payment_method_report',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Payment method report generation task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger payment method report")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerEnsureDefaultMethodView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Ensure default payment method exists",
        description="Check if a default payment method exists; if not, set the first available as default.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="EnsureDefaultMethodResponse",
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
            task = ensure_default_payment_method_exists.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_ensure_default_method',
                model_name='Controls',
                object_id='payment_method_default',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Ensure default payment method task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger ensure default method")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )