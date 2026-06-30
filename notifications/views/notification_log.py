import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from notifications.models.notification_log import NotificationLog
from notifications.serializers.notification_log import (
    NotificationLogReadSerializer,
    NotificationLogListSerializer,
    NotificationLogCreateSerializer,
    NotificationLogUpdateSerializer,
    NotificationLogRetrySerializer,
)
from notifications.services.notification_log import NotificationLogService
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

class NotificationLogListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = NotificationLogListSerializer(many=True)


class NotificationLogDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationLogReadSerializer()


class NotificationLogCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationLogReadSerializer()


class NotificationLogUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationLogReadSerializer()


class NotificationLogRetryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = NotificationLogReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class NotificationLogCRUDView(APIView):
    """
    CRUD operations for notification logs.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /notification-logs/  (list) and GET /notification-logs/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Logs"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="status", type=str, description="Filter by status (queued, sent, failed, resend)", required=False),
            OpenApiParameter(name="recipient_email", type=str, description="Filter by recipient email", required=False),
            OpenApiParameter(name="from_date", type=str, description="Filter from date", required=False),
            OpenApiParameter(name="to_date", type=str, description="Filter to date", required=False),
            OpenApiParameter(name="search", type=str, description="Search by email, subject, or payload", required=False),
        ],
        responses={
            200: NotificationLogListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single notification log (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single notification log or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view notification logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                log_entry = NotificationLogService.get_by_id(id)
                if not log_entry:
                    return _error(
                        data={"detail": "Notification log not found."},
                        message="Notification log not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = NotificationLogReadSerializer(log_entry, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="NotificationLog",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Notification log retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'status': request.query_params.get('status'),
                'recipient_email': request.query_params.get('recipient_email'),
                'from_date': request.query_params.get('from_date'),
                'to_date': request.query_params.get('to_date'),
                'search': request.query_params.get('search'),
            }
            filters = {k: v for k, v in filters.items() if v is not None}

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'created_at')
            sort_order = request.query_params.get('sort_order', 'desc')

            result = NotificationLogService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Notification logs retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="NotificationLog",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Notification log retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /notification-logs/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Logs"],
        request=NotificationLogCreateSerializer,
        responses={
            201: NotificationLogCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new notification log. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new notification log."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create notification logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = NotificationLogCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="NotificationLog",
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
            log_entry = NotificationLogService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = NotificationLogReadSerializer(log_entry, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Notification log created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification log creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create notification log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /notification-logs/<id>/
    # NOT ALLOWED - Notification logs are immutable for full updates
    # ------------------------------------------------------------------

    def put(self, request, id=None):
        return _error(
            data={"detail": "Full update of notification logs is not allowed."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # PATCH /notification-logs/<id>/
    # WITH TRANSACTION (only status/error_message)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Logs"],
        request=NotificationLogUpdateSerializer,
        responses={
            200: NotificationLogUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of a notification log (status and error_message). Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a notification log."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update notification logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        log_entry = NotificationLogService.get_by_id(id)
        if not log_entry:
            return _error(
                data={"detail": "Notification log not found."},
                message="Notification log not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NotificationLogUpdateSerializer(
            log_entry,
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
            updated = NotificationLogService.update(
                log_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = NotificationLogReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Notification log updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification log update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update notification log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /notification-logs/<id>/
    # NOT ALLOWED - Notification logs are immutable for deletion
    # ------------------------------------------------------------------

    def delete(self, request, id=None):
        return _error(
            data={"detail": "Deletion of notification logs is not allowed."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


# ----------------------------------------------------------------------
# Notification Log Retry View
# ----------------------------------------------------------------------

class NotificationLogRetryView(APIView):
    """
    Retry a failed or queued notification.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        request=NotificationLogRetrySerializer,
        responses={
            200: NotificationLogRetryResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retry a failed or queued notification. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Retry a failed or queued notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to retry notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        log_entry = NotificationLogService.get_by_id(id)
        if not log_entry:
            return _error(
                data={"detail": "Notification log not found."},
                message="Notification log not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = NotificationLogRetrySerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.instance = log_entry

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = NotificationLogService.retry_failed(
                log_id=id,
                user=user,
                request=request
            )

            read_serializer = NotificationLogReadSerializer(updated, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="notification_retry",
                model_name="NotificationLog",
                object_id=str(id),
                changes={"status": updated.status},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Notification queued for retry.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification retry failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retry notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )