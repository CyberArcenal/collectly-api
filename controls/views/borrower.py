# controls/views/borrower.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from borrowers.tasks import (
    recalculate_credit_scores,
    force_credit_score_recalc,
    merge_duplicate_borrowers,
    update_borrower_statuses,
    cleanup_incomplete_borrowers,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerCreditScoreRecalcView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Trigger credit score recalculation",
        description="Recalculate credit scores for all borrowers or specific ones.",
        request=inline_serializer(
            name="CreditScoreRecalcRequest",
            fields={
                "borrower_ids": serializers.ListField(
                    child=serializers.IntegerField(),
                    required=False,
                    help_text="Optional list of borrower IDs. If not provided, recalculates all."
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="CreditScoreRecalcResponse",
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
            borrower_ids = request.data.get('borrower_ids')
            task = force_credit_score_recalc.delay(borrower_ids=borrower_ids, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_credit_score_recalc',
                model_name='Controls',
                object_id='borrower_credit_score',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Credit score recalculation task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger credit score recalculation")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerBorrowerMergeView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Merge duplicate borrowers",
        description="Merge duplicate borrower records based on email/contact.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="BorrowerMergeResponse",
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
            task = merge_duplicate_borrowers.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_borrower_merge',
                model_name='Controls',
                object_id='borrower_merge',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Borrower merge task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger borrower merge")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerBorrowerCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Clean up incomplete borrowers",
        description="Soft-delete borrowers with missing required fields older than N days.",
        request=inline_serializer(
            name="BorrowerCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=30,
                    help_text="Age in days for borrowers to clean up"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="BorrowerCleanupResponse",
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
            task = cleanup_incomplete_borrowers.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_borrower_cleanup',
                model_name='Controls',
                object_id='borrower_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Borrower cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger borrower cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerBorrowerStatusUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Update borrower statuses",
        description="Update borrower active/inactive status based on their debts.",
        request=None,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="BorrowerStatusUpdateResponse",
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
            task = update_borrower_statuses.delay(user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_borrower_status_update',
                model_name='Controls',
                object_id='borrower_status',
                changes={'task_id': task.id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Borrower status update task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger borrower status update")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )