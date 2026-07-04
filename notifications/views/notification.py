import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from audit.utils.log import log_audit_event
from notifications.models.notification import Notification
from notifications.serializers.notification import (
    NotificationReadSerializer,
    NotificationListSerializer,
    NotificationCreateSerializer,
    NotificationUpdateSerializer,
    NotificationMarkReadSerializer,
    NotificationMarkAllReadSerializer,
)
from notifications.services.notification import NotificationService
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

class NotificationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = NotificationListSerializer(many=True)


class NotificationDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationReadSerializer()


class NotificationCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationReadSerializer()


class NotificationUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationReadSerializer()


class NotificationDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class NotificationMarkReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationReadSerializer()


class NotificationMarkAllReadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class NotificationUnreadCountResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.IntegerField()


class NotificationStatisticsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class NotificationCRUDView(APIView):
    """
    CRUD operations for notifications.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /notifications/  (list) and GET /notifications/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notifications"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="debt_id", type=int, description="Filter by debt ID", required=False),
            OpenApiParameter(name="type", type=str, description="Filter by type", required=False),
            OpenApiParameter(name="is_read", type=bool, description="Filter by read status", required=False),
            OpenApiParameter(name="search", type=str, description="Search by title or message", required=False),
            OpenApiParameter(name="from_date", type=str, description="Filter from date", required=False),
            OpenApiParameter(name="to_date", type=str, description="Filter to date", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: NotificationListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single notification (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single notification or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                notification = NotificationService.get_by_id(id, include_deleted)
                if not notification:
                    return _error(
                        data={"detail": "Notification not found."},
                        message="Notification not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = NotificationReadSerializer(notification, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="Notification",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Notification retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'debt_id': request.query_params.get('debt_id'),
                'type': request.query_params.get('type'),
                'is_read': request.query_params.get('is_read'),
                'search': request.query_params.get('search'),
                'from_date': request.query_params.get('from_date'),
                'to_date': request.query_params.get('to_date'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            filters = {k: v for k, v in filters.items() if v is not None}

            # Convert is_read to boolean
            if filters.get('is_read') is not None:
                filters['is_read'] = filters['is_read'].lower() == 'true'

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'created_at')
            sort_order = request.query_params.get('sort_order', 'desc')

            result = NotificationService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            serialized_data = NotificationListSerializer(
                result['data'],
                many=True,
                context={'request': request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Notifications retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Notification",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Notification retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /notifications/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notifications"],
        request=NotificationCreateSerializer,
        responses={
            201: NotificationCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new notification. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = NotificationCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="Notification",
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
            notification = NotificationService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = NotificationReadSerializer(notification, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Notification created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /notifications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notifications"],
        request=NotificationUpdateSerializer,
        responses={
            200: NotificationUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing notification. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        notification = NotificationService.get_by_id(id)
        if not notification:
            return _error(
                data={"detail": "Notification not found."},
                message="Notification not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NotificationUpdateSerializer(
            notification,
            data=request.data,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = NotificationService.update(
                notification_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = NotificationReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Notification updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /notifications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notifications"],
        request=NotificationUpdateSerializer,
        responses={
            200: NotificationUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing notification. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        notification = NotificationService.get_by_id(id)
        if not notification:
            return _error(
                data={"detail": "Notification not found."},
                message="Notification not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NotificationUpdateSerializer(
            notification,
            data=request.data,
            partial=True,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = NotificationService.update(
                notification_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = NotificationReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Notification updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /notifications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notifications"],
        responses={
            204: NotificationDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a notification. Admin/Staff only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to delete notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        notification = NotificationService.get_by_id(id)
        if not notification:
            return _error(
                data={"detail": "Notification not found."},
                message="Notification not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            NotificationService.delete(
                notification_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Notification deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Notification Mark Read View
# ----------------------------------------------------------------------

class NotificationMarkReadView(APIView):
    """
    Mark a notification as read or unread.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=NotificationMarkReadSerializer,
        responses={
            200: NotificationMarkReadResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Mark a notification as read or unread."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Mark a notification as read or unread."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        notification = NotificationService.get_by_id(id)
        if not notification:
            return _error(
                data={"detail": "Notification not found."},
                message="Notification not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NotificationMarkReadSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.instance = notification

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = serializer.save()

            read_serializer = NotificationReadSerializer(updated, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="notification_read",
                model_name="Notification",
                object_id=str(id),
                changes={"is_read": updated.is_read},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message=f"Notification marked as {'read' if updated.is_read else 'unread'}.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification mark read failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update notification read status.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Notification Mark All Read View
# ----------------------------------------------------------------------

class NotificationMarkAllReadView(APIView):
    """
    Mark all notifications as read.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=NotificationMarkAllReadSerializer,
        responses={
            200: NotificationMarkAllReadResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Mark all notifications as read. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Mark all notifications as read."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to mark all notifications as read."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = NotificationMarkAllReadSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = NotificationService.mark_all_as_read(
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="notifications_read_all",
                model_name="Notification",
                object_id="all",
                changes={"count": result['count']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Marked {result['count']} notifications as read.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Mark all read failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to mark all notifications as read.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Notification Unread Count View
# ----------------------------------------------------------------------

class NotificationUnreadCountView(APIView):
    """
    Get count of unread notifications.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        responses={
            200: NotificationUnreadCountResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get the count of unread notifications."
    )
    def get(self, request):
        """Get unread notification count."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view notification counts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            count = NotificationService.get_unread_count()

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Notification",
                object_id="count",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=count,
                message="Unread notification count retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Unread count error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve unread notification count.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Notification Statistics View
# ----------------------------------------------------------------------

class NotificationStatisticsView(APIView):
    """
    Get notification statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        responses={
            200: NotificationStatisticsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get notification statistics including counts by type and read status."
    )
    def get(self, request):
        """Get notification statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view notification statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = NotificationService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="Notification",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Notification statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Notification statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve notification statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            
            
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# NOTIFICATION RESTORE VIEW
# ===================================================================

class NotificationRestoreView(APIView):
    """
    Restore a soft-deleted notification. Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        responses={
            200: NotificationDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted notification. Admin only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to restore notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            notification = NotificationService.restore(
                notification_id=id,
                user=user,
                request=request
            )

            serializer = NotificationReadSerializer(notification, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="Notification",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Notification restored successfully.",
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
            logger.exception("Notification restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION PERMANENT DELETE VIEW
# ===================================================================

class NotificationPermanentDeleteView(APIView):
    """
    Permanently delete a notification (hard delete). Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        responses={
            204: NotificationDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a notification (hard delete). Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to permanently delete notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            NotificationService.permanent_delete(
                notification_id=id,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="Notification",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Notification permanently deleted successfully.",
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
            logger.exception("Notification permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION MARK MANY READ VIEW
# ===================================================================

class NotificationMarkManyReadView(APIView):
    """
    Mark multiple notifications as read.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=inline_serializer(
            name="MarkManyReadRequest",
            fields={
                "ids": serializers.ListField(
                    child=serializers.IntegerField(),
                    help_text="List of notification IDs to mark as read"
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="MarkManyReadResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Mark multiple notifications as read. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Mark multiple notifications as read."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to mark notifications as read."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        ids = request.data.get("ids")
        if not ids or not isinstance(ids, list):
            return _error(
                data={"detail": "ids must be a non-empty list."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = NotificationService.mark_many_as_read(
                notification_ids=ids,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="notifications_mark_many_read",
                model_name="Notification",
                object_id="many",
                changes={"count": result['updated_count']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"updatedCount": result['updated_count']},
                message=f"Marked {result['updated_count']} notifications as read.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Mark many read failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to mark notifications as read.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION BULK CREATE VIEW
# ===================================================================

class NotificationBulkCreateView(APIView):
    """
    Bulk create multiple notifications. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=inline_serializer(
            name="BulkCreateRequest",
            fields={
                "notificationsArray": serializers.ListField(
                    child=NotificationCreateSerializer()
                ),
            }
        ),
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": NotificationReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk create multiple notifications. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create multiple notifications."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        notifications_data = request.data.get("notificationsArray")
        if not isinstance(notifications_data, list):
            return _error(
                data={"detail": "notificationsArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = NotificationService.bulk_create(
                notifications_data, 
                user=user, 
                request=request
            )
            
            created_serialized = NotificationReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_create",
                model_name="Notification",
                object_id="bulk",
                changes={"count": len(result['created']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "created": created_serialized,
                    "errors": result['errors']
                },
                message="Bulk create completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk create failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk create notifications.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION BULK UPDATE VIEW
# ===================================================================

class NotificationBulkUpdateView(APIView):
    """
    Bulk update multiple notifications. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=inline_serializer(
            name="BulkUpdateRequest",
            fields={
                "updatesArray": serializers.ListField(
                    child=serializers.DictField()
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="BulkUpdateResponse",
                fields={
                    "updated": NotificationReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk update multiple notifications. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request):
        """Bulk update multiple notifications."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        updates = request.data.get("updatesArray")
        if not isinstance(updates, list):
            return _error(
                data={"detail": "updatesArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = NotificationService.bulk_update(
                updates, 
                user=user, 
                request=request
            )
            
            updated_serialized = NotificationReadSerializer(
                result['updated'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_update",
                model_name="Notification",
                object_id="bulk",
                changes={"count": len(result['updated']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "updated": updated_serialized,
                    "errors": result['errors']
                },
                message="Bulk update completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk update notifications.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION IMPORT VIEW
# ===================================================================

class NotificationImportView(APIView):
    """
    Import notifications from CSV content. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=inline_serializer(
            name="ImportRequest",
            fields={
                "fileContent": serializers.CharField(),
                "fileName": serializers.CharField(required=False),
            }
        ),
        responses={
            201: inline_serializer(
                name="ImportResponse",
                fields={
                    "imported": NotificationReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Import notifications from CSV content. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Import notifications from CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to import notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        file_content = request.data.get("fileContent")
        if not file_content:
            return _error(
                data={"detail": "fileContent is required."},
                message="Invalid request.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import csv
            from io import StringIO
            reader = csv.DictReader(StringIO(file_content))
            notifications_data = list(reader)
        except Exception as e:
            return _error(
                data={"detail": f"Invalid CSV: {str(e)}"},
                message="CSV parsing error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = NotificationService.bulk_create(
                notifications_data, 
                user=user, 
                request=request
            )
            
            imported_serialized = NotificationReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="import_csv",
                model_name="Notification",
                object_id="import",
                changes={"count": len(result['created']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "imported": imported_serialized,
                    "errors": result['errors']
                },
                message="CSV import completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("CSV import failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to import notifications.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION EXPORT VIEW
# ===================================================================

class NotificationExportView(APIView):
    """
    Export notifications to CSV or JSON.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notifications"],
        request=inline_serializer(
            name="ExportRequest",
            fields={
                "format": serializers.ChoiceField(choices=["csv", "json"], default="json"),
                "filters": serializers.DictField(required=False),
            }
        ),
        responses={
            200: inline_serializer(
                name="ExportResponse",
                fields={
                    "format": serializers.CharField(),
                    "data": serializers.CharField(help_text="CSV string or JSON array"),
                    "filename": serializers.CharField()
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Export notifications to CSV or JSON."
    )
    def post(self, request):
        """Export notifications."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to export notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.data.get("format", "json")
        filters = request.data.get("filters", {})

        try:
            exported_data = NotificationService.export_notifications(filters)
            
            if fmt == "csv":
                import csv
                from io import StringIO
                output = StringIO()
                if exported_data:
                    fieldnames = exported_data[0].keys()
                    writer = csv.DictWriter(output, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(exported_data)
                data_str = output.getvalue()
                filename = f"notifications_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # json
                import json
                data_str = json.dumps(exported_data, default=str)
                filename = f"notifications_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"

            log_audit_event(
                request=request,
                user=user,
                action_type="export",
                model_name="Notification",
                object_id="export",
                changes={"format": fmt, "count": len(exported_data)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "format": fmt,
                    "data": data_str,
                    "filename": filename
                },
                message="Export completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Export failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to export notifications.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )