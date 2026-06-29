# users/views/User.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from django.db import transaction
from django.core.exceptions import ValidationError

from audit.utils.log import log_audit_event
from users.permissions.base import IsAccountActive, IsAdmin
from users.serializers.User import (
    UserReadSerializer,
    UserListSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
)
from rest_framework import serializers
from utils.response import CustomPagination, _success, _error

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes

import logging

logger = logging.getLogger(__name__)

User = get_user_model()


# ----------------------------------------------------------------------
# Response serializers for documentation (matching CustomPagination)
# ----------------------------------------------------------------------


class PaginationMetadataSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""

    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class PaginatedUserListResponseSerializer(serializers.Serializer):
    """Response for GET /users/ (paginated list)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    pagination = PaginationMetadataSerializer()
    data = UserListSerializer(many=True)


class SingleUserResponseSerializer(serializers.Serializer):
    """Response for GET /users/<pk>/ (single user)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = UserReadSerializer()


class UserCreateResponseSerializer(serializers.Serializer):
    """Response for POST /users/ (201 Created)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = UserReadSerializer()


class UserUpdateResponseSerializer(serializers.Serializer):
    """Response for PUT/PATCH /users/<pk>/ (200 OK)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = UserReadSerializer()


class UserDeleteResponseSerializer(serializers.Serializer):
    """Response for DELETE /users/<pk>/ (204 No Content)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = serializers.DictField(required=False, allow_null=True)


class ErrorResponseSerializer(serializers.Serializer):
    """Generic error response"""

    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.JSONField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------


class UserCRUDView(APIView):
    """
    CRUD operations for users. Only accessible to active admin users.
    """

    permission_classes = [IsAuthenticated, IsAccountActive, IsAdmin]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /users/  (list)  and  GET /users/<pk>/  (retrieve)
    # No transaction needed for read operations
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["User Management"],
        parameters=[
            OpenApiParameter(
                name="page",
                type=int,
                description="Page number for pagination",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=int,
                description="Number of items per page",
                required=False,
            ),
        ],
        responses={
            200: PaginatedUserListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
        description="Retrieve a single user (if pk provided) or a paginated list of all users.",
    )
    def get(self, request, pk=None):
        """Retrieve single user or list all users"""
        try:
            if pk:
                user = User.objects.get(pk=pk, is_deleted=False)
                serializer = UserReadSerializer(user, context={"request": request})
                log_audit_event(
                    request=request,
                    user=request.user,
                    action_type="read",
                    model_name="User",
                    object_id=str(pk),
                    changes={"status": user.status, "user_type": user.user_type},
                )
                return _success(
                    data=serializer.data, message="User retrieved successfully."
                )
            else:
                users = User.objects.filter(is_deleted=False)
                paginator = self.pagination_class()
                page = paginator.paginate_queryset(users, request)
                serializer = UserListSerializer(
                    page, many=True, context={"request": request}
                )
                log_audit_event(
                    request=request,
                    user=request.user,
                    action_type="read",
                    model_name="User",
                    object_id="list",
                    changes={"count": users.count()},
                )
                return paginator.get_paginated_response(
                    data=serializer.data, message="Users retrieved successfully."
                )

        except User.DoesNotExist:
            log_audit_event(
                request=request,
                user=request.user,
                action_type="read",
                model_name="User",
                object_id=str(pk),
                changes={"error": "User not found"},
            )
            return _error(
                data=[], message="User not found.", status=status.HTTP_404_NOT_FOUND
            )

    # ------------------------------------------------------------------
    # POST /users/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["User Management"],
        request=UserCreateSerializer,
        responses={
            201: UserCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
        },
        description="Create a new user. Admin only.",
    )
    @transaction.atomic
    def post(self, request):
        """Create new user with atomic transaction."""
        serializer = UserCreateSerializer(
            data=request.data, context={"request": request}
        )

        # Validate first - if invalid, rollback and return error
        if not serializer.is_valid():
            transaction.set_rollback(True)  # Force rollback
            log_audit_event(
                request=request,
                user=request.user,
                action_type="create",
                model_name="User",
                object_id="new",
                changes={"error": serializer.errors, "data": request.data},
            )
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = serializer.save()
            log_audit_event(
                request=request,
                user=request.user,
                action_type="create",
                model_name="User",
                object_id=str(user.pk),
                changes={"status": user.status, "user_type": user.user_type},
            )
            return _success(
                data=UserReadSerializer(user, context={"request": request}).data,
                message="User created successfully.",
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            # Rollback on any unexpected error
            transaction.set_rollback(True)
            logger.exception(f"User creation failed: {e}")
            return _error(
                data={"detail": str(e)},
                message="Failed to create user.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /users/<pk>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["User Management"],
        request=UserUpdateSerializer,
        responses={
            200: UserUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
        description="Full update of an existing user. Admin only.",
    )
    @transaction.atomic
    def put(self, request, pk):
        """Full update with atomic transaction."""
        try:
            user = User.objects.get(pk=pk, is_deleted=False)
        except User.DoesNotExist:
            return _error(
                data=[], message="User not found.", status=status.HTTP_404_NOT_FOUND
            )

        serializer = UserUpdateSerializer(
            user, data=request.data, context={"request": request}, partial=True
        )

        if not serializer.is_valid(raise_exception=True):
            transaction.set_rollback(True)  # Force rollback
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
            log_audit_event(
                request=request,
                user=request.user,
                action_type="update",
                model_name="User",
                object_id=str(pk),
                changes=serializer.validated_data,
            )
            return _success(
                data=UserReadSerializer(user, context={"request": request}).data,
                message="User updated successfully.",
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            transaction.set_rollback(True)
            logger.exception(f"User update failed for user {pk}: {e}")
            return _error(
                data={"detail": str(e)},
                message="Failed to update user.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /users/<pk>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["User Management"],
        request=UserUpdateSerializer,
        responses={
            200: UserUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
        description="Partial update of an existing user. Admin only.",
    )
    @transaction.atomic
    def patch(self, request, pk):
        """Partial update with atomic transaction."""
        try:
            user = User.objects.get(pk=pk, is_deleted=False)
        except User.DoesNotExist:
            return _error(
                data=[], message="User not found.", status=status.HTTP_404_NOT_FOUND
            )

        serializer = UserUpdateSerializer(
            user, data=request.data, partial=True, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)  # Force rollback
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
            log_audit_event(
                request=request,
                user=request.user,
                action_type="partial_update",
                model_name="User",
                object_id=str(pk),
                changes=serializer.validated_data,
            )
            return _success(
                data=UserReadSerializer(user, context={"request": request}).data,
                message="User updated successfully.",
            )
        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"User partial update failed for user {pk}: {e}")
            return _error(
                data={"detail": str(e)},
                message="Failed to update user.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /users/<pk>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["User Management"],
        responses={
            204: UserDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
        description="Soft delete a user (sets is_deleted=True). Admin only.",
    )
    @transaction.atomic
    def delete(self, request, pk):
        """Soft delete with atomic transaction."""
        try:
            user = User.objects.get(pk=pk, is_deleted=False)
        except User.DoesNotExist:
            return _error(
                data=[], message="User not found.", status=status.HTTP_404_NOT_FOUND
            )

        try:
            user.delete()
            log_audit_event(
                request=request,
                user=request.user,
                action_type="delete",
                model_name="User",
                object_id=str(pk),
                changes={"status": "soft-deleted"},
            )
            return Response(
                {
                    "status": True,
                    "message": "User soft-deleted successfully.",
                    "data": None,
                },
                status=status.HTTP_204_NO_CONTENT,
            )
        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"User deletion failed for user {pk}: {e}")
            return _error(
                data={"detail": str(e)},
                message="Failed to delete user.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
