# audit/views/log.py
import logging
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from audit.models.log import AuditLog
from audit.serializers.AuditLog import (
    AuditLogReadSerializer,
    AuditLogListSerializer,
    AuditLogWriteSerializer,
)
from audit.services.log import AuditLogService
from audit.utils.log import log_audit_event
from notifications.views.notification import ErrorResponseSerializer
from users.permissions.base import IsAccountActive, is_admin, is_staff
from utils.response import BasePaginatedSerializer, CustomPagination, _success, _error
from utils.security import get_client_ip

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation (matching CustomPagination)
# ----------------------------------------------------------------------


class AuditLogListResponseSerializer(serializers.Serializer):
    """Full response for GET /audit-logs/ (paginated list)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = AuditLogListSerializer(many=True)


class AuditLogDetailResponseDataSerializer(serializers.Serializer):
    """Response data for GET /audit-logs/<id>/ (single)"""

    data = AuditLogReadSerializer()


class AuditLogDetailResponseSerializer(serializers.Serializer):
    """Full response for GET /audit-logs/<id>/ (single)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditLogReadSerializer()


class AuditLogCreateResponseDataSerializer(serializers.Serializer):
    """Response data for POST /audit-logs/ (201 Created)"""

    data = AuditLogReadSerializer()


class AuditLogCreateResponseSerializer(serializers.Serializer):
    """Full response for POST /audit-logs/ (201 Created)"""

    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditLogReadSerializer()


class AuditLogErrorResponseSerializer(serializers.Serializer):
    """Generic error response"""

    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


class AuditLogValidationErrorSerializer(serializers.Serializer):
    """Validation error response (400)"""

    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()
    data = serializers.DictField(required=False, allow_null=True)


class AuditLogStatsDataSerializer(serializers.Serializer):
    """Response data for audit log statistics."""

    total_logs = serializers.IntegerField()
    suspicious_count = serializers.IntegerField()
    days = serializers.IntegerField()
    action_distribution = serializers.ListField(child=serializers.DictField())
    model_distribution = serializers.ListField(child=serializers.DictField())
    top_users = serializers.ListField(child=serializers.DictField())


class AuditLogStatsResponseSerializer(serializers.Serializer):
    """Full response for audit log statistics."""

    status = serializers.BooleanField()
    message = serializers.CharField()
    data = AuditLogStatsDataSerializer()


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------


class AuditLogCRUD(APIView):
    """
    CRUD operations for audit logs.
    - AuditLogs are immutable; updates are not allowed.
    - Admin/Staff users can view and delete logs.
    - Regular users can only view their own logs.
    """

    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        IsAccountActive,
    ]

    # ------------------------------------------------------------------
    # GET /audit-logs/  (list) and GET /audit-logs/<id>/ (retrieve)
    # No transaction needed for read operations
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="action_type",
                type=str,
                description="Filter by action type",
                required=False,
            ),
            OpenApiParameter(
                name="user_id",
                type=int,
                description="Filter by user ID",
                required=False,
            ),
            OpenApiParameter(
                name="model_name",
                type=str,
                description="Filter by model name",
                required=False,
            ),
            OpenApiParameter(
                name="object_id",
                type=str,
                description="Filter by object ID",
                required=False,
            ),
            OpenApiParameter(
                name="suspicious",
                type=bool,
                description="Filter by suspicious status",
                required=False,
            ),
            OpenApiParameter(
                name="start",
                type=str,
                description="Start date (ISO datetime)",
                required=False,
            ),
            OpenApiParameter(
                name="end",
                type=str,
                description="End date (ISO datetime)",
                required=False,
            ),
            OpenApiParameter(
                name="q",
                type=str,
                description="Free-text search",
                required=False,
            ),
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
            200: AuditLogListResponseSerializer,
            401: AuditLogErrorResponseSerializer,
            403: AuditLogErrorResponseSerializer,
            404: AuditLogErrorResponseSerializer,
            500: AuditLogErrorResponseSerializer,
        },
        description=(
            "Retrieve a single audit log (if id provided) or a paginated list of all logs "
            "with optional filters. Admin users can access all logs; regular users can only access their own."
        ),
        examples=[
            OpenApiExample(
                "List response",
                value={
                    "status": True,
                    "message": "Success",
                    "pagination": {
                        "next": "http://example.com/api/v1/audit-logs/?page=2&page_size=10",
                        "previous": None,
                        "count": 25,
                        "current_page": 1,
                        "total_pages": 3,
                        "page_size": 10,
                    },
                    "data": [
                        {
                            "id": 1,
                            "event_id": "c3f9a2d0-1234-5678-9abc-def012345678",
                            "user_display": "johndoe",
                            "action_type": "login",
                            "action_type_display": "Login",
                            "model_name": "User",
                            "object_id": "1",
                            "is_suspicious": False,
                            "timestamp": "2025-01-01T00:00:00Z",
                        }
                    ],
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request, id=None):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "read"

        try:
            if id is not None:
                if is_admin(user) or is_staff(user):
                    log_obj = AuditLog.objects.filter(id=id).first()
                else:
                    log_obj = AuditLog.objects.filter(id=id, user=user).first()

                if not log_obj:
                    return _error(
                        data={"detail": "Audit log not found."},
                        message="Audit log not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = AuditLogReadSerializer(
                    log_obj, context={"request": request}
                )

                # Log this read operation (but don't create infinite loop)
                # In production, you might want to skip logging audit log reads
                if not request.query_params.get("skip_audit"):
                    pass

                return _success(
                    data=serializer.data,
                    message="Audit log retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List logs with filters
            if is_admin(user) or is_staff(user):
                qs = AuditLog.objects.all().order_by("-timestamp")
            else:
                qs = AuditLog.objects.filter(user=user).order_by("-timestamp")

            # Apply filters
            action = request.query_params.get("action_type")
            user_id = request.query_params.get("user_id")
            model_name = request.query_params.get("model_name")
            object_id = request.query_params.get("object_id")
            suspicious = request.query_params.get("suspicious")
            start = request.query_params.get("start")
            end = request.query_params.get("end")
            search = request.query_params.get("q")

            if action:
                qs = qs.filter(action_type=action)
            if user_id and (is_admin(user) or is_staff(user)):
                qs = qs.filter(user_id=user_id)
            if model_name:
                qs = qs.filter(model_name=model_name)
            if object_id:
                qs = qs.filter(object_id=object_id)
            if suspicious is not None:
                val = suspicious.lower() in {"1", "true", "yes"}
                qs = qs.filter(is_suspicious=val)
            if start:
                dt = parse_datetime(start)
                if dt:
                    qs = qs.filter(timestamp__gte=dt)
            if end:
                dt = parse_datetime(end)
                if dt:
                    qs = qs.filter(timestamp__lte=dt)
            if search:
                qs = qs.filter(
                    Q(action_type__icontains=search)
                    | Q(model_name__icontains=search)
                    | Q(object_id__icontains=search)
                    | Q(user_agent__icontains=search)
                )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = AuditLogListSerializer(
                page, many=True, context={"request": request}
            )
            response = paginator.get_paginated_response(
                data=serializer.data, message="Audit logs retrieved successfully."
            )

            return response

        except Exception as exc:
            logger.exception("Audit log retrieval error")
            return _error(
                data={"detail": "An error occurred."},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /audit-logs/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only (or system)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Logs"],
        request=AuditLogWriteSerializer,
        responses={
            201: AuditLogCreateResponseSerializer,
            400: AuditLogValidationErrorSerializer,
            401: AuditLogErrorResponseSerializer,
            403: AuditLogErrorResponseSerializer,
            500: AuditLogErrorResponseSerializer,
        },
        description=(
            "Create a new audit log entry. Admin/Staff only. "
            "Audit logs are immutable and cannot be updated or deleted."
        ),
        examples=[
            OpenApiExample(
                "Create audit log request",
                value={
                    "user": 1,
                    "action_type": "login",
                    "model_name": "User",
                    "object_id": "1",
                    "changes": {"detail": "User logged in"},
                    "ip_address": "192.168.1.100",
                    "user_agent": "Mozilla/5.0...",
                    "is_suspicious": False,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "id": 1,
                        "event_id": "c3f9a2d0-1234-5678-9abc-def012345678",
                        "user": 1,
                        "user_display": "johndoe",
                        "action_type": "login",
                        "action_type_display": "Login",
                        "model_name": "User",
                        "object_id": "1",
                        "changes": {"detail": "User logged in"},
                        "ip_address": "192.168.1.100",
                        "user_agent": "Mozilla/5.0...",
                        "is_suspicious": False,
                        "suspicious_reason": None,
                        "timestamp": "2025-01-01T00:00:00Z",
                        "summary": "[login] User (1) by johndoe",
                    },
                },
                response_only=True,
                status_codes=["201"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "create"

        # Only admin/staff can create audit logs
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to create audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        # Set IP and user agent if not provided
        if "ip_address" not in request.data or not request.data["ip_address"]:
            request.data["ip_address"] = client_ip
        if "user_agent" not in request.data or not request.data["user_agent"]:
            request.data["user_agent"] = user_agent

        serializer = AuditLogWriteSerializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log_obj = serializer.save()

            read_serializer = AuditLogReadSerializer(
                log_obj, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Audit log created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit log creation failed: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create audit log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /audit-logs/<id>/
    # NOT ALLOWED - AuditLogs are immutable
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Logs"],
        request=AuditLogWriteSerializer,
        responses={
            405: inline_serializer(
                name="MethodNotAllowedResponse",
                fields={
                    "status": serializers.BooleanField(default=False),
                    "detail": serializers.CharField(),
                },
            ),
        },
        description="Audit logs are immutable. PUT is not allowed.",
        exclude=True,
    )
    def put(self, request, id=None):
        return _error(
            data={"detail": "Audit logs are immutable and cannot be updated."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # PATCH /audit-logs/<id>/
    # NOT ALLOWED - AuditLogs are immutable
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Logs"],
        request=AuditLogWriteSerializer,
        responses={
            405: inline_serializer(
                name="MethodNotAllowedResponse",
                fields={
                    "status": serializers.BooleanField(default=False),
                    "detail": serializers.CharField(),
                },
            ),
        },
        description="Audit logs are immutable. PATCH is not allowed.",
        exclude=True,
    )
    def patch(self, request, id=None):
        return _error(
            data={"detail": "Audit logs are immutable and cannot be updated."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # DELETE /audit-logs/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only (with caution)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Logs"],
        responses={
            204: inline_serializer(
                name="DeleteSuccessResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            ),
            401: AuditLogErrorResponseSerializer,
            403: AuditLogErrorResponseSerializer,
            404: AuditLogErrorResponseSerializer,
            500: AuditLogErrorResponseSerializer,
        },
        description=(
            "Delete an audit log. Admin/Staff only. "
            "This operation cannot be undone and should be used with caution."
        ),
        examples=[
            OpenApiExample(
                "Success response",
                value={"status": True, "message": "Audit log deleted successfully."},
                response_only=True,
                status_codes=["204"],
            ),
            OpenApiExample(
                "Not found",
                value={"status": False, "detail": "Audit log not found."},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    @transaction.atomic
    def delete(self, request, id):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "delete"

        # Only admin/staff can delete audit logs
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to delete audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            log_obj = AuditLog.objects.get(id=id)

            # Store data before deletion
            log_data = AuditLogReadSerializer(log_obj).data

            log_obj.delete()

            return _success(
                data=None,
                message="Audit log deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except AuditLog.DoesNotExist:
            return _error(
                data={"detail": "Audit log not found."},
                message="Audit log not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit log deletion failed: {exc}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete audit log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Audit Log Statistics View
# ----------------------------------------------------------------------




class AuditLogStatsView(APIView):
    """
    Get enhanced audit log statistics for dashboard.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="days",
                type=int,
                description="Number of days to look back",
                required=False,
                default=7,
            ),
        ],
        responses={
            200: inline_serializer(
                name="AuditLogStatsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="AuditLogStatsData",
                        fields={
                            "total": serializers.IntegerField(),
                            "totalToday": serializers.IntegerField(),
                            "uniqueUsers": serializers.IntegerField(),
                            "avgPerDay": serializers.FloatField(),
                            "mostActiveDay": serializers.DictField(
                                allow_null=True,
                                child=serializers.DictField()
                            ),
                            "dateRange": serializers.DictField(
                                allow_null=True,
                                child=serializers.CharField()
                            ),
                            "byAction": serializers.ListField(
                                child=serializers.DictField()
                            ),
                            "byEntity": serializers.ListField(
                                child=serializers.DictField()
                            ),
                            "byUser": serializers.ListField(
                                child=serializers.DictField()
                            ),
                        }
                    ),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get enhanced audit log statistics including totals, unique users, and averages.",
    )
    def get(self, request):
        try:
            days = int(request.query_params.get("days", 7))
            stats = AuditLogService.get_enhanced_statistics(days)

            return _success(
                data=stats,
                message="Audit log statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Audit log stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve audit log statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# AUDIT LOG BY ENTITY VIEW
# ===================================================================


class AuditLogByEntityView(APIView):
    """
    Get audit logs filtered by entity.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="entity", type=str, description="Entity name", required=True
            ),
            OpenApiParameter(
                name="entityId",
                type=int,
                description="Optional entity ID",
                required=False,
            ),
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get audit logs filtered by entity.",
    )
    def get(self, request):
        """Get audit logs by entity."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        entity = request.query_params.get("entity")
        if not entity:
            return _error(
                data={"detail": "entity parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            entity_id = request.query_params.get("entityId")
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 50))

            result = AuditLogService.get_logs_by_entity(
                entity=entity, entity_id=entity_id, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialize_data = AuditLogListSerializer(
                result["data"], many=True, context={"request": request}
            ).data
            response = paginator.get_paginated_response(
                data=serialize_data,
                message="Audit logs by entity retrieved successfully.",
                pagination=result["pagination"],
            )

            return response

        except Exception as exc:
            logger.exception("Audit logs by entity error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG BY USER VIEW
# ===================================================================


class AuditLogByUserView(APIView):
    """
    Get audit logs filtered by user.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="user", type=str, description="Username", required=True
            ),
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get audit logs filtered by user.",
    )
    def get(self, request):
        """Get audit logs by user."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        username = request.query_params.get("user")
        if not username:
            return _error(
                data={"detail": "user parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 50))

            result = AuditLogService.get_logs_by_user(
                username=username, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialize_data = AuditLogListSerializer(
                result["data"], many=True, context={"request": request}
            ).data
            response = paginator.get_paginated_response(
                data=serialize_data,
                message="Audit logs by user retrieved successfully.",
                pagination=result["pagination"],
            )

            return response

        except Exception as exc:
            logger.exception("Audit logs by user error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG BY ACTION VIEW
# ===================================================================


class AuditLogByActionView(APIView):
    """
    Get audit logs filtered by action.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="action", type=str, description="Action type", required=True
            ),
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get audit logs filtered by action.",
    )
    def get(self, request):
        """Get audit logs by action."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        action = request.query_params.get("action")
        if not action:
            return _error(
                data={"detail": "action parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 50))

            result = AuditLogService.get_logs_by_action(
                action=action, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialize_data = AuditLogListSerializer(
                result["data"], many=True, context={"request": request}
            ).data
            response = paginator.get_paginated_response(
                data=serialize_data,
                message="Audit logs by action retrieved successfully.",
                pagination=result["pagination"],
            )

            return response

        except Exception as exc:
            logger.exception("Audit logs by action error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG BY DATE RANGE VIEW
# ===================================================================


class AuditLogByDateRangeView(APIView):
    """
    Get audit logs within a date range.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="startDate",
                type=str,
                description="Start date (ISO)",
                required=True,
            ),
            OpenApiParameter(
                name="endDate", type=str, description="End date (ISO)", required=True
            ),
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get audit logs within a date range.",
    )
    def get(self, request):
        """Get audit logs by date range."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        if not start_date or not end_date:
            return _error(
                data={"detail": "startDate and endDate parameters are required."},
                message="Missing required parameters.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 50))

            result = AuditLogService.get_logs_by_date_range(
                start_date=start_date, end_date=end_date, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialize_data = AuditLogListSerializer(
                result["data"], many=True, context={"request": request}
            ).data
            response = paginator.get_paginated_response(
                data=serialize_data,
                message="Audit logs by date range retrieved successfully.",
                pagination=result["pagination"],
            )

            return response

        except Exception as exc:
            logger.exception("Audit logs by date range error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG SEARCH VIEW
# ===================================================================


class AuditLogSearchView(APIView):
    """
    Search audit logs by keyword.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="searchTerm", type=str, description="Search keyword", required=True
            ),
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Search audit logs by keyword.",
    )
    def get(self, request):
        """Search audit logs."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to search audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        search_term = request.query_params.get("searchTerm")
        if not search_term:
            return _error(
                data={"detail": "searchTerm parameter is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 50))

            result = AuditLogService.search_logs(
                search_term=search_term, page=page, limit=limit
            )

            paginator = self.pagination_class()
            serialize_data = AuditLogListSerializer(
                result["data"], many=True, context={"request": request}
            ).data
            response = paginator.get_paginated_response(
                data=serialize_data,
                message="Search completed successfully.",
                pagination=result["pagination"],
            )

            return response

        except Exception as exc:
            logger.exception("Audit log search error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG SUMMARY VIEW
# ===================================================================


class AuditLogSummaryView(APIView):
    """
    Get grouped summary of audit logs.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="startDate",
                type=str,
                description="Start date (ISO)",
                required=False,
            ),
            OpenApiParameter(
                name="endDate", type=str, description="End date (ISO)", required=False
            ),
        ],
        responses={
            200: inline_serializer(
                name="AuditLogSummaryResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get grouped summary of audit logs by action, entity, and user.",
    )
    def get(self, request):
        """Get audit log summary."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit summary."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            start_date = request.query_params.get("startDate")
            end_date = request.query_params.get("endDate")

            summary = AuditLogService.get_summary(
                start_date=start_date, end_date=end_date
            )

            return _success(
                data=summary,
                message="Audit summary retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Audit summary error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve audit summary.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG COUNTS VIEW
# ===================================================================


class AuditLogCountsView(APIView):
    """
    Get aggregated counts of audit logs.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="startDate",
                type=str,
                description="Start date (ISO)",
                required=False,
            ),
            OpenApiParameter(
                name="endDate", type=str, description="End date (ISO)", required=False
            ),
        ],
        responses={
            200: inline_serializer(
                name="AuditLogCountsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get aggregated counts grouped by action, entity, and user.",
    )
    def get(self, request):
        """Get audit log counts."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view audit counts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            start_date = request.query_params.get("startDate")
            end_date = request.query_params.get("endDate")

            counts = AuditLogService.get_counts(
                start_date=start_date, end_date=end_date
            )

            return _success(
                data=counts,
                message="Audit counts retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Audit counts error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve audit counts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG TOP ACTIVITIES VIEW
# ===================================================================


class AuditLogTopActivitiesView(APIView):
    """
    Get top activities (most frequent actions, entities, users).
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                description="Number of top items",
                required=False,
                default=10,
            ),
            OpenApiParameter(
                name="startDate",
                type=str,
                description="Start date (ISO)",
                required=False,
            ),
            OpenApiParameter(
                name="endDate", type=str, description="End date (ISO)", required=False
            ),
        ],
        responses={
            200: inline_serializer(
                name="AuditLogTopActivitiesResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get top activities (most frequent actions, entities, users).",
    )
    def get(self, request):
        """Get top activities."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view top activities."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.query_params.get("limit", 10))
            start_date = request.query_params.get("startDate")
            end_date = request.query_params.get("endDate")

            result = AuditLogService.get_top_activities(
                limit=limit, start_date=start_date, end_date=end_date
            )

            return _success(
                data=result,
                message="Top activities retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Top activities error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve top activities.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG RECENT ACTIVITY VIEW
# ===================================================================


class AuditLogRecentActivityView(APIView):
    """
    Get recent audit log activity.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                description="Number of entries",
                required=False,
                default=10,
            ),
        ],
        responses={
            200: inline_serializer(
                name="AuditLogRecentActivityResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get recent audit log activity (latest entries).",
    )
    def get(self, request):
        """Get recent activity."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to view recent activity."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.query_params.get("limit", 10))

            result = AuditLogService.get_recent_activity(limit=limit)

            return _success(
                data=result,
                message="Recent activity retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Recent activity error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve recent activity.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG EXPORT VIEW
# ===================================================================


class AuditLogExportView(APIView):
    """
    Export audit logs to CSV.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        request=inline_serializer(
            name="ExportRequest",
            fields={
                "searchTerm": serializers.CharField(required=False),
                "entity": serializers.CharField(required=False),
                "user": serializers.CharField(required=False),
                "action": serializers.CharField(required=False),
                "startDate": serializers.CharField(required=False),
                "endDate": serializers.CharField(required=False),
                "limit": serializers.IntegerField(required=False, default=5000),
            },
        ),
        responses={
            200: inline_serializer(
                name="ExportResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Export audit logs to CSV.",
    )
    @transaction.atomic
    def post(self, request):
        """Export audit logs to CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to export audit logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            filters = {
                "search_term": request.data.get("searchTerm"),
                "entity": request.data.get("entity"),
                "user": request.data.get("user"),
                "action": request.data.get("action"),
                "start_date": request.data.get("startDate"),
                "end_date": request.data.get("endDate"),
            }
            # Remove None values
            filters = {k: v for k, v in filters.items() if v is not None}

            limit = request.data.get("limit", 5000)

            result = AuditLogService.export_logs_to_csv(filters=filters, limit=limit)

            log_audit_event(
                request=request,
                user=user,
                action_type="audit_export",
                model_name="AuditLog",
                object_id="export",
                changes={"filters": filters, "limit": limit},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message="Audit logs exported successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Audit export error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to export audit logs.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# AUDIT LOG GENERATE REPORT VIEW
# ===================================================================


class AuditLogGenerateReportView(APIView):
    """
    Generate an audit report (JSON or HTML).
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Audit Logs"],
        request=inline_serializer(
            name="GenerateReportRequest",
            fields={
                "startDate": serializers.CharField(required=False),
                "endDate": serializers.CharField(required=False),
                "format": serializers.ChoiceField(
                    choices=["json", "html"], default="json"
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="GenerateReportResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Generate a comprehensive audit report (JSON or HTML).",
    )
    @transaction.atomic
    def post(self, request):
        """Generate audit report."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user) and not is_staff(user):
            return _error(
                data={
                    "detail": "You do not have permission to generate audit reports."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            start_date = request.data.get("startDate")
            end_date = request.data.get("endDate")
            format_type = request.data.get("format", "json")

            result = AuditLogService.generate_report(
                start_date=start_date, end_date=end_date, format=format_type
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="audit_export",
                model_name="AuditLog",
                object_id="report",
                changes={"format": format_type, "count": result["entryCount"]},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message="Audit report generated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Audit report error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to generate audit report.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
