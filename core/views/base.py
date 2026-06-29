# core/views/base.py
from django.db import connections
from django.db.utils import OperationalError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny

from drf_spectacular.utils import (
    extend_schema,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes


# ----------------------------------------------------------------------
# Health Check Response Serializer
# ----------------------------------------------------------------------

class HealthCheckResponseSerializer(serializers.Serializer):
    """Response serializer for health check endpoint."""
    status = serializers.CharField(help_text="Overall status (ok/error)")
    database = serializers.CharField(help_text="Database connection status (ok/error)")
    version = serializers.CharField(help_text="API version")


# ----------------------------------------------------------------------
# Health Check View
# ----------------------------------------------------------------------

class HealthCheckView(APIView):
    """
    Health check endpoint for monitoring and load balancers.
    Returns database connectivity status and API version.
    """
    permission_classes = [AllowAny]
    authentication_classes = []  # No authentication required

    @extend_schema(
        tags=["System"],
        responses={
            200: HealthCheckResponseSerializer,
        },
        description=(
            "Health check endpoint to verify service availability. "
            "Checks database connectivity and returns API version."
        ),
        examples=[
            OpenApiExample(
                "Health check response",
                value={
                    "status": "ok",
                    "database": "ok",
                    "version": "1.0.0",
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Database error response",
                value={
                    "status": "error",
                    "database": "error",
                    "version": "1.0.0",
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request):
        """
        Perform health check and return status.
        """
        db_status = "ok"
        try:
            connections['default'].cursor()
        except OperationalError:
            db_status = "error"

        return Response({
            "status": "ok" if db_status == "ok" else "error",
            "database": db_status,
            "version": "1.0.0",
        })