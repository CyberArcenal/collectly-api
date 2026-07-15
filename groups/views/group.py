import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from groups.models.debtor_group import DebtorGroup
from groups.models.debtor_group_member import DebtorGroupMember
from groups.serializers.debtor_group import (
    DebtorGroupReadSerializer,
    DebtorGroupListSerializer,
    DebtorGroupCreateSerializer,
    DebtorGroupUpdateSerializer,
)
from groups.serializers.debtor_group_member import (
    DebtorGroupMemberReadSerializer,
    DebtorGroupMemberListSerializer,
    DebtorGroupMemberCreateSerializer,
    DebtorGroupMemberDeleteSerializer,
)
from groups.services.group import GroupService
from users.permissions.base import IsAccountActive, can_read, can_edit, is_admin
from utils.response import BasePaginatedSerializer, CustomPagination, _success, _error
from utils.security import get_client_ip

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------


class GroupListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = DebtorGroupListSerializer(many=True)


class GroupDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtorGroupReadSerializer()


class GroupCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtorGroupReadSerializer()


class GroupUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtorGroupReadSerializer()


class GroupDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


class GroupStatsItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    member_count = serializers.IntegerField()
    total_debt = serializers.FloatField()


class GroupOverallStatsDataSerializer(serializers.Serializer):
    total_groups = serializers.IntegerField()
    average_members = serializers.FloatField()
    groups_with_zero_members = serializers.IntegerField()
    groups = GroupStatsItemSerializer(many=True)


class GroupOverallStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = GroupOverallStatsDataSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ============================================================
# GROUP CRUD VIEW
# ============================================================


class GroupCRUDView(APIView):
    """
    CRUD operations for debtor groups.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /groups/  (list) and GET /groups/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Groups"],
        parameters=[
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
            OpenApiParameter(
                name="search",
                type=str,
                description="Search by name or description",
                required=False,
            ),
        ],
        responses={
            200: GroupListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single group (if id provided) or a paginated list of groups.",
    )
    def get(self, request, id=None):
        """Retrieve single group or list all groups."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                group = GroupService.get_group_by_id(id)
                if not group:
                    return _error(
                        data={"detail": "Group not found."},
                        message="Group not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = DebtorGroupReadSerializer(
                    group, context={"request": request}
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="DebtorGroup",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Group retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            search = request.query_params.get("search")
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 20))

            result = GroupService.get_groups(page=page, limit=limit, search=search)

            paginator = self.pagination_class()
            serialized_data = DebtorGroupListSerializer(
                result["data"], many=True, context={"request": request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Groups retrieved successfully.",
                pagination=result["pagination"],
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="DebtorGroup",
                object_id="list",
                changes={"count": result["pagination"]["total"]},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Group retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /groups/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Groups"],
        request=DebtorGroupCreateSerializer,
        responses={
            201: GroupCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new group. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        """Create a new group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DebtorGroupCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="DebtorGroup",
                object_id="new",
                changes={"error": serializer.errors, "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            group = GroupService.create_group(
                data=serializer.validated_data, user=user, request=request
            )

            read_serializer = DebtorGroupReadSerializer(
                group, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Group created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Group creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create group.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /groups/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Groups"],
        request=DebtorGroupUpdateSerializer,
        responses={
            200: GroupUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing group. Admin/Staff only.",
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        group = GroupService.get_group_by_id(id)
        if not group:
            return _error(
                data={"detail": "Group not found."},
                message="Group not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DebtorGroupUpdateSerializer(
            group, data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = GroupService.update_group(
                group_id=id, data=serializer.validated_data, user=user, request=request
            )

            read_serializer = DebtorGroupReadSerializer(
                updated, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Group updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Group update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update group.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /groups/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Groups"],
        request=DebtorGroupUpdateSerializer,
        responses={
            200: GroupUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing group. Admin/Staff only.",
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        group = GroupService.get_group_by_id(id)
        if not group:
            return _error(
                data={"detail": "Group not found."},
                message="Group not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DebtorGroupUpdateSerializer(
            group, data=request.data, partial=True, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = GroupService.update_group(
                group_id=id, data=serializer.validated_data, user=user, request=request
            )

            read_serializer = DebtorGroupReadSerializer(
                updated, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Group updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Group partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update group.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /groups/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Groups"],
        responses={
            204: GroupDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a group (cascade to members). Admin only.",
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to delete groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        group = GroupService.get_group_by_id(id)
        if not group:
            return _error(
                data={"detail": "Group not found."},
                message="Group not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            GroupService.delete_group(group_id=id, user=user, request=request)

            return _success(
                data=None,
                message="Group deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Group deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete group.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# GROUP MEMBER CRUD VIEW
# ============================================================


class GroupMemberCRUDView(APIView):
    """
    CRUD operations for group members.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /groups/<group_id>/members/  (list)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Group Members"],
        parameters=[
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
            OpenApiParameter(
                name="group_id", type=int, description="Group ID", required=True
            ),
        ],
        responses={
            200: inline_serializer(
                name="GroupMemberListResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "pagination": BasePaginatedSerializer(),
                    "data": DebtorGroupMemberListSerializer(many=True),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get paginated list of members in a group.",
    )
    def get(self, request):
        """Get members of a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view group members."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            group_id = request.query_params.get("group_id")
            if not group_id:
                return _error(
                    data={"detail": "group_id parameter is required."},
                    message="Missing required parameter.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 20))

            result = GroupService.get_members(group_id=group_id, page=page, limit=limit)

            paginator = self.pagination_class()
            serialized_data = DebtorGroupMemberListSerializer(
                result["data"], many=True, context={"request": request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Group members retrieved successfully.",
                pagination=result["pagination"],
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="DebtorGroupMember",
                object_id="list",
                changes={"group_id": group_id},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Group members retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /groups/<group_id>/members/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Group Members"],
        request=DebtorGroupMemberCreateSerializer,
        responses={
            201: inline_serializer(
                name="GroupMemberCreateResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": DebtorGroupMemberReadSerializer(),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Add a debtor to a group. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        """Add a member to a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to add members to groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DebtorGroupMemberCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="DebtorGroupMember",
                object_id="new",
                changes={"error": serializer.errors, "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = serializer.validated_data
            member = GroupService.add_member(
                group_id=data["group"].id,
                debtor_id=data["debtor"].id,
                user=user,
                request=request,
            )

            read_serializer = DebtorGroupMemberReadSerializer(
                member, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Member added successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Add member failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to add member.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /groups/<group_id>/members/<debtor_id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Group Members"],
        parameters=[
            OpenApiParameter(
                name="group_id", type=int, description="Group ID", required=True
            ),
            OpenApiParameter(
                name="debtor_id", type=int, description="Debtor ID", required=True
            ),
        ],
        responses={
            204: inline_serializer(
                name="GroupMemberDeleteResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Remove a debtor from a group. Admin/Staff only.",
    )
    @transaction.atomic
    def delete(self, request):
        """Remove a member from a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to remove members from groups."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            group_id = request.query_params.get("group_id")
            debtor_id = request.query_params.get("debtor_id")

            if not group_id or not debtor_id:
                return _error(
                    data={"detail": "group_id and debtor_id parameters are required."},
                    message="Missing required parameters.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            member = GroupService.remove_member(
                group_id=group_id, debtor_id=debtor_id, user=user, request=request
            )

            return _success(
                data=None,
                message="Member removed successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Remove member failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to remove member.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# GROUP STATISTICS VIEW
# ============================================================


class GroupStatsView(APIView):
    """
    Get group statistics.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Groups"],
        parameters=[
            OpenApiParameter(
                name="group_id",
                type=int,
                description="Group ID",
                required=True,
            ),
        ],
        responses={
            200: inline_serializer(
                name="GroupStatsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get statistics for a group including member count and total debt.",
    )
    def get(self, request):
        """Get group statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view group statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            group_id = request.query_params.get("group_id")
            if not group_id:
                return _error(
                    data={"detail": "group_id parameter is required."},
                    message="Missing required parameter.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            stats = GroupService.get_group_stats(group_id)

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="DebtorGroup",
                object_id=str(group_id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Group statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Group stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve group statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# GROUPS FOR DEBTOR VIEW
# ===================================================================


class GroupsForDebtorView(APIView):
    """
    Get groups for a specific debtor.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Groups"],
        parameters=[
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: GroupListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get all groups a debtor belongs to.",
    )
    def get(self, request, debtor_id):
        """Get groups for a specific debtor."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view groups."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            # Check if debtor exists
            debtor = Borrower.objects.filter(
                id=debtor_id, deleted_at__isnull=True
            ).first()
            if not debtor:
                return _error(
                    data={"detail": "Debtor not found."},
                    message="Debtor not found.",
                    status=status.HTTP_404_NOT_FOUND,
                )

            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 20))

            result = GroupService.get_groups_for_borrower(
                borrower_id=debtor_id, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialized_data = DebtorGroupListSerializer(
                result["data"], many=True, context={"request": request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Groups for debtor retrieved successfully.",
                pagination=result["pagination"],
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="DebtorGroup",
                object_id="by_debtor",
                changes={"debtor_id": debtor_id},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Groups for debtor retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# BULK ASSIGN VIEW
# ===================================================================


class GroupBulkAssignView(APIView):
    """
    Bulk assign debtors to a group.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Groups"],
        request=inline_serializer(
            name="BulkAssignRequest",
            fields={
                "debtorIds": serializers.ListField(
                    child=serializers.IntegerField(),
                    help_text="List of debtor IDs to assign",
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="BulkAssignResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk assign multiple debtors to a group. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request, group_id):
        """Bulk assign debtors to a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to assign members to groups."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debtor_ids = request.data.get("debtorIds")
        if not debtor_ids or not isinstance(debtor_ids, list):
            return _error(
                data={"detail": "debtorIds must be a non-empty list."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = GroupService.bulk_assign(
                group_id=group_id, debtor_ids=debtor_ids, user=user, request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_assign",
                model_name="DebtorGroupMember",
                object_id="bulk",
                changes={
                    "group_id": group_id,
                    "assigned_count": result["assigned_count"],
                    "errors_count": len(result.get("errors", [])),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "assignedCount": result["assigned_count"],
                    "errors": result.get("errors", []),
                },
                message=f"Bulk assign completed: {result['assigned_count']} assigned.",
                status=status.HTTP_200_OK,
            )

        except ValidationError as e:
            return _error(
                data=e.message_dict,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk assign failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk assign debtors.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# CLEAR MEMBERS VIEW
# ===================================================================


class GroupClearMembersView(APIView):
    """
    Clear all members from a group.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Groups"],
        responses={
            204: inline_serializer(
                name="ClearMembersResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Remove all members from a group. Admin/Staff only.",
    )
    @transaction.atomic
    def delete(self, request, group_id):
        """Clear all members from a group."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to clear group members."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = GroupService.clear_members(
                group_id=group_id, user=user, request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="clear_members",
                model_name="DebtorGroup",
                object_id=str(group_id),
                changes={"members_removed": result["members_removed"]},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message=f"Cleared {result['members_removed']} members from group.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except ValidationError as e:
            return _error(
                data=e.message_dict,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Clear members failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to clear group members.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# REMOVE MEMBER ALTERNATIVE PATH VIEW
# ===================================================================


class GroupRemoveMemberView(APIView):
    """
    Remove a debtor from a group (alternative RESTful path).
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Group Members"],
        responses={
            204: inline_serializer(
                name="GroupMemberDeleteResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Remove a debtor from a group. Admin/Staff only.",
    )
    @transaction.atomic
    def delete(self, request, group_id, debtor_id):
        """Remove a member from a group using path parameters."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to remove members from groups."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            member = GroupService.remove_member(
                group_id=group_id, debtor_id=debtor_id, user=user, request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="remove_member",
                model_name="DebtorGroupMember",
                object_id=str(member.id),
                changes={"group_id": group_id, "debtor_id": debtor_id},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Member removed successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except ValidationError as e:
            return _error(
                data=e.message_dict,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Remove member failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to remove member.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ============================================================
# View
# ============================================================


class GroupOverallStatsView(APIView):
    """
    Get overall group statistics.

    Returns aggregate statistics across all groups:
    - total number of groups
    - average members per group
    - count of groups with zero members
    - detailed list of each group with member count and total outstanding debt
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Groups"],
        summary="Get overall group statistics",
        description="Returns aggregate statistics across all groups including member counts and total debt per group.",
        responses={
            200: GroupOverallStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                name="Success Response",
                value={
                    "status": True,
                    "message": "Group overall statistics retrieved successfully.",
                    "data": {
                        "total_groups": 5,
                        "average_members": 12.4,
                        "groups_with_zero_members": 1,
                        "groups": [
                            {
                                "id": 1,
                                "name": "VIP",
                                "member_count": 25,
                                "total_debt": 150000.50,
                            },
                            {
                                "id": 2,
                                "name": "High-Risk",
                                "member_count": 18,
                                "total_debt": 85000.00,
                            },
                            {
                                "id": 3,
                                "name": "Corporate",
                                "member_count": 0,
                                "total_debt": 0.0,
                            },
                        ],
                    },
                },
                status_codes=["200"],
            ),
            OpenApiExample(
                name="Unauthorized",
                value={
                    "status": False,
                    "message": "Authentication credentials were not provided.",
                    "data": None,
                },
                status_codes=["401"],
            ),
            OpenApiExample(
                name="Forbidden",
                value={
                    "status": False,
                    "message": "You do not have permission to view group statistics.",
                    "data": None,
                },
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request):
        try:
            stats = GroupService.get_overall_statistics()
            return _success(
                data=stats,
                message="Group overall statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.exception("Group overall stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve group statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
