# audit/views/log.py
import logging
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from analytics.serializers.LicenseUsage.read import LicenseUsageListSerializer
from audit.models.log import AuditLog
from audit.serializers.AuditLog import (
    AuditLogReadSerializer,
    AuditLogListSerializer,
    AuditLogWriteSerializer,
)
from audit.services.log import AuditLogService
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
    data = AuditLogListSerializer()


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
                        ]
                    
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
                qs = AuditLog.objects.all().order_by('-timestamp')
            else:
                qs = AuditLog.objects.filter(user=user).order_by('-timestamp')

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
                    Q(action_type__icontains=search) |
                    Q(model_name__icontains=search) |
                    Q(object_id__icontains=search) |
                    Q(user_agent__icontains=search)
                )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = AuditLogListSerializer(
                page, many=True, context={"request": request}
            )
            response = paginator.get_paginated_response(
                data=serializer.data,
                message="Audit logs retrieved successfully."
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
                    }
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
                }
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
                }
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
                }
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
    Get audit log statistics.
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
            200: AuditLogStatsResponseSerializer,
            401: AuditLogErrorResponseSerializer,
            403: AuditLogErrorResponseSerializer,
            500: AuditLogErrorResponseSerializer,
        },
        description=(
            "Get audit log statistics including total counts, suspicious count, "
            "action distribution, model distribution, and top users."
        ),
        examples=[
            OpenApiExample(
                "Stats response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "total_logs": 1000,
                        "suspicious_count": 5,
                        "days": 7,
                        "action_distribution": [
                            {"action_type": "login", "count": 500},
                            {"action_type": "read", "count": 300},
                            {"action_type": "update", "count": 200},
                        ],
                        "model_distribution": [
                            {"model_name": "User", "count": 400},
                            {"model_name": "License", "count": 300},
                            {"model_name": "Activation", "count": 300},
                        ],
                        "top_users": [
                            {"user__username": "admin", "count": 200},
                            {"user__username": "johndoe", "count": 150},
                        ],
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request):
        try:
            days = int(request.query_params.get("days", 7))

            stats = AuditLogService.get_log_statistics(days)

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