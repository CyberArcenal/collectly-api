# controls/views/loan_agreement.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from loan_agreements.tasks import (
    cleanup_old_draft_agreements,
    notify_overdue_agreements,
    auto_assign_agreements,
    sync_agreement_statuses,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerAgreementCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Clean up old draft agreements",
        description="Soft-delete draft agreements that have not been signed for N days.",
        request=inline_serializer(
            name="AgreementCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=30,
                    help_text="Age in days for draft agreements to delete"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="AgreementCleanupResponse",
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
            task = cleanup_old_draft_agreements.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_agreement_cleanup',
                model_name='Controls',
                object_id='loan_agreement_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Agreement cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger agreement cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerOverdueAgreementNotifyView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Notify overdue signed agreements",
        description="Check for signed agreements with overdue debts and notify admins.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="OverdueAgreementNotifyResponse",
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
            task = notify_overdue_agreements.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_overdue_agreement_notify',
                model_name='Controls',
                object_id='loan_agreement_overdue_notify',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Overdue agreement notification task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger overdue agreement notification")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerAutoAssignAgreementsView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Auto-assign agreements",
        description="Create draft agreements for debts that do not have one.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="AutoAssignAgreementsResponse",
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
            task = auto_assign_agreements.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_auto_assign_agreements',
                model_name='Controls',
                object_id='loan_agreement_auto_assign',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Auto-assign agreements task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger auto-assign agreements")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerSyncAgreementStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Sync agreement statuses with debts",
        description="Check for signed agreements linked to paid debts and notify.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="SyncAgreementStatusResponse",
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
            task = sync_agreement_statuses.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_sync_agreement_status',
                model_name='Controls',
                object_id='loan_agreement_sync_status',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Sync agreement statuses task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger sync agreement statuses")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )