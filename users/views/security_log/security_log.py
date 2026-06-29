# users/views/security_log.py
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework import serializers
from datetime import datetime
from django.db import transaction
import logging

from users.models.security_log import SecurityLog
from users.permissions.base import is_staff
from users.serializers.SecurityLog.read import SecurityLogReadSerializer
from users.utils.authentications import IsAuthenticatedAndNotBlacklisted
from utils.response import CustomPagination, _success, _error

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
)
from drf_spectacular.types import OpenApiTypes

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class SecurityLogPaginationMetadataSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""
    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class SecurityLogListResponseSerializer(serializers.Serializer):
    """Response for GET /security-logs/ (paginated list)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    pagination = SecurityLogPaginationMetadataSerializer()
    data = SecurityLogReadSerializer(many=True)


class SecurityLogErrorResponseSerializer(serializers.Serializer):
    """Error response"""
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class SecurityLogListAPIView(APIView):
    """
    GET -> List of security logs for the current user
           (Admin can see all logs)
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Security Logs"],
        parameters=[
            OpenApiParameter(
                name="event_type",
                type=str,
                description="Filter by event type (login, logout, password_change, etc.)",
                required=False,
            ),
            OpenApiParameter(
                name="ip_address",
                type=str,
                description="Filter by IP address (partial match)",
                required=False,
            ),
            OpenApiParameter(
                name="start_date",
                type=str,
                description="Filter by start date (YYYY-MM-DD)",
                required=False,
            ),
            OpenApiParameter(
                name="end_date",
                type=str,
                description="Filter by end date (YYYY-MM-DD)",
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
            200: SecurityLogListResponseSerializer,
            401: SecurityLogErrorResponseSerializer,
            403: SecurityLogErrorResponseSerializer,
            500: SecurityLogErrorResponseSerializer,
        },
        description=(
            "Retrieve a paginated list of security logs. "
            "Regular users can only see their own logs; "
            "admin users can see all logs."
        ),
    )
    def get(self, request):
        try:
            if is_staff(request.user):
                logs = SecurityLog.objects.all().order_by("-created_at")
            else:
                logs = SecurityLog.objects.filter(user=request.user).order_by("-created_at")

            # Apply filters
            event_type = request.GET.get("event_type")
            start_date = request.GET.get("start_date")
            end_date = request.GET.get("end_date")
            ip_address = request.GET.get("ip_address")

            if event_type:
                logs = logs.filter(event_type=event_type)
            if ip_address:
                logs = logs.filter(ip_address__icontains=ip_address)
            if start_date:
                try:
                    start_date = datetime.strptime(start_date, "%Y-%m-%d")
                    logs = logs.filter(created_at__gte=start_date)
                except ValueError:
                    pass
            if end_date:
                try:
                    end_date = datetime.strptime(end_date, "%Y-%m-%d")
                    logs = logs.filter(created_at__lte=end_date)
                except ValueError:
                    pass

            # Pagination
            paginator = self.pagination_class()
            page = paginator.paginate_queryset(logs, request)
            serializer = SecurityLogReadSerializer(page, many=True, context={"request": request})

            return paginator.get_paginated_response(
                data=serializer.data,
                message="Security logs retrieved successfully."
            )

        except Exception as e:
            logger.exception("Error retrieving security logs")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security logs.",
                status=500,
            )