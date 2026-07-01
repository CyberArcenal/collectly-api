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
            


from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# NOTIFICATION LOG BY RECIPIENT VIEW
# ===================================================================

class NotificationLogByRecipientView(APIView):
    """
    Get notification logs by recipient email.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        parameters=[
            OpenApiParameter(name="recipient_email", type=str, description="Recipient email", required=True),
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
        ],
        responses={
            200: NotificationLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get paginated notification logs for a specific recipient."
    )
    def get(self, request):
        """Get notification logs by recipient."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view notification logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        recipient_email = request.query_params.get('recipient_email')
        if not recipient_email:
            return _error(
                data={"detail": "recipient_email parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))

            result = NotificationLogService.get_by_recipient(
                recipient_email=recipient_email,
                page=page,
                limit=limit
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
                object_id="by_recipient",
                changes={"recipient_email": recipient_email},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Notification logs by recipient error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION LOG SEARCH VIEW
# ===================================================================

class NotificationLogSearchView(APIView):
    """
    Search notification logs by keyword.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        parameters=[
            OpenApiParameter(name="keyword", type=str, description="Search keyword", required=True),
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
        ],
        responses={
            200: NotificationLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Search notification logs by keyword in email, subject, or payload."
    )
    def get(self, request):
        """Search notification logs."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to search notification logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        keyword = request.query_params.get('keyword')
        if not keyword:
            return _error(
                data={"detail": "keyword parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))

            result = NotificationLogService.search(
                keyword=keyword,
                page=page,
                limit=limit
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Search completed successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="search",
                model_name="NotificationLog",
                object_id="search",
                changes={"keyword": keyword},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Notification log search error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION LOG RESEND VIEW
# ===================================================================

class NotificationLogResendView(APIView):
    """
    Resend a notification (manual resend).
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        request=inline_serializer(
            name="ResendRequest",
            fields={
                "confirm": serializers.BooleanField(),
            }
        ),
        responses={
            200: NotificationLogRetryResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Resend a notification (manual resend). Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Resend a notification."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to resend notifications."},
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

        confirm = request.data.get('confirm', False)
        if not confirm:
            return _error(
                data={"detail": "Please confirm to resend this notification."},
                message="Confirmation required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = NotificationLogService.resend(
                log_id=id,
                user=user,
                request=request
            )

            read_serializer = NotificationLogReadSerializer(updated, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="notification_resend",
                model_name="NotificationLog",
                object_id=str(id),
                changes={"resend_count": updated.resend_count},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Notification queued for resend.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Notification resend failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to resend notification.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION LOG RETRY ALL VIEW
# ===================================================================

class NotificationLogRetryAllView(APIView):
    """
    Retry all failed notifications.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        request=inline_serializer(
            name="RetryAllRequest",
            fields={
                "filters": serializers.DictField(required=False, help_text="Optional filters (recipient_email, created_before)"),
                "confirm": serializers.BooleanField(),
            }
        ),
        responses={
            200: inline_serializer(
                name="RetryAllResponse",
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
        description="Retry all failed notifications. Admin only."
    )
    @transaction.atomic
    def post(self, request):
        """Retry all failed notifications."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to retry all failed notifications."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        confirm = request.data.get('confirm', False)
        if not confirm:
            return _error(
                data={"detail": "Please confirm to retry all failed notifications."},
                message="Confirmation required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        filters = request.data.get('filters', {})

        try:
            result = NotificationLogService.retry_all_failed(
                filters=filters,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="notification_retry_all",
                model_name="NotificationLog",
                object_id="all_failed",
                changes=result,
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Retry all completed: {result['processed']} processed, {result['errors']} errors.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Retry all failed failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retry all failed notifications.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# NOTIFICATION LOG STATISTICS VIEW
# ===================================================================

class NotificationLogStatsView(APIView):
    """
    Get notification log statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Notification Logs"],
        parameters=[
            OpenApiParameter(name="start_date", type=str, description="Start date (YYYY-MM-DD)", required=False),
            OpenApiParameter(name="end_date", type=str, description="End date (YYYY-MM-DD)", required=False),
        ],
        responses={
            200: inline_serializer(
                name="NotificationStatsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get notification log statistics including counts by status."
    )
    def get(self, request):
        """Get notification log statistics."""
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
            stats = NotificationLogService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="NotificationLog",
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
            logger.exception("Notification stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve notification statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )