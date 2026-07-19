# controls/views/group.py
import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer

from users.permissions.base import IsAccountActive, can_edit
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from groups.tasks.group import (
    bulk_assign_borrowers_to_group,
    update_group_statistics,
    cleanup_orphaned_memberships,
    auto_assign_borrowers_to_groups,
)
from controls.serializers import TaskTriggerResponseSerializer, ErrorResponseSerializer

logger = logging.getLogger(__name__)


class TriggerBulkAssignView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Bulk assign borrowers to a group",
        description="Asynchronously assign multiple borrowers to a group.",
        request=inline_serializer(
            name="BulkAssignRequest",
            fields={
                "group_id": serializers.IntegerField(help_text="ID of the group"),
                "debtor_ids": serializers.ListField(
                    child=serializers.IntegerField(),
                    help_text="List of borrower IDs to assign"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="BulkAssignResponse",
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
            group_id = request.data.get('group_id')
            debtor_ids = request.data.get('debtor_ids', [])
            if not group_id:
                return _error(
                    data={"detail": "group_id is required."},
                    message="Missing required field.",
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not debtor_ids:
                return _error(
                    data={"detail": "debtor_ids list is required."},
                    message="Missing required field.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            task = bulk_assign_borrowers_to_group.delay(
                group_id=group_id,
                debtor_ids=debtor_ids,
                user=user.username
            )
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_bulk_assign',
                model_name='Controls',
                object_id='group_bulk_assign',
                changes={'task_id': task.id, 'group_id': group_id, 'count': len(debtor_ids)},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Bulk assign task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger bulk assign")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerAutoAssignView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Auto-assign borrowers to a group based on criteria",
        description="Find borrowers matching criteria and assign them to a group.",
        request=inline_serializer(
            name="AutoAssignRequest",
            fields={
                "group_id": serializers.IntegerField(help_text="ID of the group"),
                "criteria": serializers.DictField(
                    help_text="Filter criteria (e.g., {'credit_rating': 'Good', 'total_debt__gte': 5000})"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="AutoAssignResponse",
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
            group_id = request.data.get('group_id')
            criteria = request.data.get('criteria', {})
            if not group_id:
                return _error(
                    data={"detail": "group_id is required."},
                    message="Missing required field.",
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not criteria:
                return _error(
                    data={"detail": "criteria is required."},
                    message="Missing required field.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            task = auto_assign_borrowers_to_groups.delay(
                group_id=group_id,
                criteria=criteria,
                user=user.username
            )
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_auto_assign',
                model_name='Controls',
                object_id='group_auto_assign',
                changes={'task_id': task.id, 'group_id': group_id, 'criteria': criteria},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Auto-assign task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger auto-assign")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerGroupCleanupView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Cleanup orphaned group memberships",
        description="Remove group memberships where the borrower has been deleted.",
        request=inline_serializer(
            name="GroupCleanupRequest",
            fields={
                "days": serializers.IntegerField(
                    default=30,
                    help_text="Only process memberships older than this many days"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="GroupCleanupResponse",
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
            task = cleanup_orphaned_memberships.delay(days=days, user=user.username)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_group_cleanup',
                model_name='Controls',
                object_id='group_cleanup',
                changes={'task_id': task.id, 'days': days},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Group cleanup task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger group cleanup")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TriggerGroupStatsUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Controls"],
        summary="Update group statistics",
        description="Recalculate statistics for all groups or a specific group.",
        request=inline_serializer(
            name="GroupStatsRequest",
            fields={
                "group_id": serializers.IntegerField(
                    required=False,
                    help_text="Optional specific group ID to update"
                )
            }
        ),
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name="GroupStatsResponse",
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
            group_id = request.data.get('group_id')
            task = update_group_statistics.delay(group_id=group_id)
            log_audit_event(
                request=request,
                user=user,
                action_type='trigger_group_stats_update',
                model_name='Controls',
                object_id='group_stats',
                changes={'task_id': task.id, 'group_id': group_id},
                ip_address=get_client_ip(request),
            )
            return _success(
                data={'task_id': task.id, 'status': 'queued'},
                message='Group stats update task triggered.',
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            logger.exception("Failed to trigger group stats update")
            return _error(
                data={"detail": str(e)},
                message="Failed to trigger task.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )