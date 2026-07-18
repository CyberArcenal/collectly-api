import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from django.core.cache import cache

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

# Import tasks
from debts.tasks.debt import (
    force_interest_accrual,
    get_interest_accrual_stats,
    force_overdue_correction,
    get_overdue_corrector_status,
    force_overdue_update,
    get_overdue_updater_status,
    force_zero_balance_fix,
    get_zero_balance_fixer_status,
    check_overdue_status_health,
    check_zero_balance_health,
)
from payments.tasks.payment import (
    force_penalty_application,
    get_penalty_scheduler_status,
    check_penalty_application_health,
)

logger = logging.getLogger(__name__)

# ============================================================
# COMMON RESPONSE SERIALIZERS
# ============================================================

class TaskTriggerResponseSerializer(serializers.Serializer):
    task_id = serializers.CharField(help_text="Celery task ID")
    status = serializers.CharField(default="queued", help_text="Task status")


class TaskStatusResponseSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    last_run = serializers.DictField(allow_null=True)
    is_running = serializers.BooleanField()
    schedule = serializers.CharField(allow_null=True)


class HealthCheckResponseSerializer(serializers.Serializer):
    issues_found = serializers.IntegerField()
    issues = serializers.ListField(child=serializers.DictField())


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


# ============================================================
# INTEREST ACCRUAL
# ============================================================

class TriggerInterestAccrualView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger interest accrual task",
        description="Manually trigger the daily interest accrual background task.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="InterestAccrualTriggerResponse",
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
            task = force_interest_accrual.delay()
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_interest_accrual',
                model_name='Controls',
                object_id='interest_accrual',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Interest accrual task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger interest accrual")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class InterestAccrualStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Get interest accrual status",
        description="Retrieve the current status and last run information for the interest accrual scheduler.",
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="InterestAccrualStatusResponse",
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
            stats = get_interest_accrual_stats()
            return _success(
                data=stats,
                message="Interest accrual status retrieved.",
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("Failed to get interest accrual status")
            return _error(
                data={"detail": str(e)},
                message="Failed to get status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# OVERDUE CORRECTOR
# ============================================================

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


# ============================================================
# OVERDUE UPDATER
# ============================================================

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


# ============================================================
# ZERO BALANCE FIXER
# ============================================================

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


# ============================================================
# PENALTY SCHEDULER
# ============================================================

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


# ============================================================
# HEALTH CHECKS
# ============================================================

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