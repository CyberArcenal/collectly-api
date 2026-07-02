# core/views/base.py
import time
import logging
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

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Version Configuration
# ----------------------------------------------------------------------

# Server version - update this with each release
SERVER_VERSION = "1.0.0"

# Minimum client version that is compatible with this server
MIN_CLIENT_VERSION = "1.0.0"

# Database schema version expected by this server
EXPECTED_SCHEMA_VERSION = "1"

# Features supported by this server
SUPPORTED_FEATURES = [
    "sync",
    "offline_mode",
    "audit_logs",
    "analytics",
    "notifications",
    "loan_management",
    "payment_tracking",
    "penalty_management",
    "group_management",
    "credit_check",
    "loan_agreements",
    "loan_applications",
]


# ----------------------------------------------------------------------
# Health Check Response Serializer
# ----------------------------------------------------------------------

class HealthCheckResponseSerializer(serializers.Serializer):
    """Response serializer for health check endpoint."""
    status = serializers.CharField(help_text="Overall status (ok/error)")
    database = serializers.CharField(help_text="Database connection status (ok/error)")
    version = serializers.CharField(help_text="API version")


# ----------------------------------------------------------------------
# Handshake Request Serializer
# ----------------------------------------------------------------------

class HandshakeRequestSerializer(serializers.Serializer):
    """Request serializer for handshake endpoint."""
    client_version = serializers.CharField(help_text="Client application version")
    db_schema_version = serializers.CharField(help_text="Client database schema version")
    platform = serializers.CharField(help_text="Platform (electron, web, mobile)")
    license_key = serializers.CharField(required=False, allow_blank=True, help_text="License key")
    timestamp = serializers.DateTimeField(help_text="Request timestamp")


# ----------------------------------------------------------------------
# Handshake Response Serializer
# ----------------------------------------------------------------------

class HandshakeResponseSerializer(serializers.Serializer):
    """Response serializer for handshake endpoint."""
    status = serializers.ChoiceField(
        choices=['ok', 'outdated', 'error'],
        help_text="Handshake status"
    )
    server_version = serializers.CharField(help_text="Server version")
    min_client_version = serializers.CharField(help_text="Minimum client version required")
    expected_schema_version = serializers.CharField(help_text="Expected database schema version")
    sync_enabled = serializers.BooleanField(help_text="Whether sync is enabled")
    ping_ms = serializers.IntegerField(help_text="Response time in milliseconds")
    features = serializers.ListField(
        child=serializers.CharField(),
        help_text="List of supported features"
    )
    required_version = serializers.CharField(
        required=False,
        help_text="Required version if client is outdated"
    )
    message = serializers.CharField(
        required=False,
        help_text="Additional message"
    )


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
            "version": SERVER_VERSION,
        })


# ----------------------------------------------------------------------
# Handshake View
# ----------------------------------------------------------------------

class HandshakeView(APIView):
    """
    Handshake endpoint for initial client-server communication.
    
    Validates client version compatibility, checks database schema,
    and returns server capabilities and sync settings.
    """
    permission_classes = [AllowAny]
    authentication_classes = []  # No authentication required

    @extend_schema(
        tags=["System"],
        request=HandshakeRequestSerializer,
        responses={
            200: HandshakeResponseSerializer,
            400: inline_serializer(
                name="HandshakeErrorResponse",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                }
            ),
        },
        description=(
            "Handshake endpoint for initial client-server communication. "
            "Validates client version compatibility, checks database schema, "
            "and returns server capabilities and sync settings."
        ),
        examples=[
            OpenApiExample(
                "Successful handshake",
                value={
                    "status": "ok",
                    "server_version": "1.0.0",
                    "min_client_version": "1.0.0",
                    "expected_schema_version": "1",
                    "sync_enabled": True,
                    "ping_ms": 42,
                    "features": ["sync", "audit_logs", "analytics"],
                    "message": "Handshake successful"
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Outdated client",
                value={
                    "status": "outdated",
                    "server_version": "1.0.0",
                    "min_client_version": "1.0.0",
                    "expected_schema_version": "1",
                    "sync_enabled": True,
                    "ping_ms": 45,
                    "features": ["sync", "audit_logs", "analytics"],
                    "required_version": "1.0.0",
                    "message": "Client version is outdated. Please update."
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Schema mismatch",
                value={
                    "status": "error",
                    "server_version": "1.0.0",
                    "min_client_version": "1.0.0",
                    "expected_schema_version": "1",
                    "sync_enabled": True,
                    "ping_ms": 43,
                    "features": ["sync", "audit_logs", "analytics"],
                    "message": "Database schema version mismatch. Please run migrations."
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request):
        """
        Handle handshake request and return compatibility information.
        """
        start_time = time.time()

        # Validate request data
        serializer = HandshakeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "status": "error",
                    "message": "Invalid request data",
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data
        client_version = validated_data.get('client_version', '0.0.0')
        db_schema_version = validated_data.get('db_schema_version', '0')
        platform = validated_data.get('platform', 'unknown')
        license_key = validated_data.get('license_key', '')

        # Log the handshake attempt
        logger.info(
            f"Handshake received: client={client_version}, "
            f"schema={db_schema_version}, platform={platform}"
        )

        # Build response
        response_data = {
            'server_version': SERVER_VERSION,
            'min_client_version': MIN_CLIENT_VERSION,
            'expected_schema_version': EXPECTED_SCHEMA_VERSION,
            'sync_enabled': True,
            'features': SUPPORTED_FEATURES,
        }

        # --- Check client version compatibility ---
        client_ok = _is_version_compatible(client_version, MIN_CLIENT_VERSION)
        if not client_ok:
            response_data['status'] = 'outdated'
            response_data['required_version'] = MIN_CLIENT_VERSION
            response_data['message'] = (
                f"Client version {client_version} is outdated. "
                f"Minimum required version is {MIN_CLIENT_VERSION}. Please update."
            )
        else:
            response_data['status'] = 'ok'
            response_data['message'] = 'Handshake successful'

        # --- Check database schema compatibility ---
        if db_schema_version != EXPECTED_SCHEMA_VERSION:
            response_data['status'] = 'error'
            response_data['message'] = (
                f"Database schema version {db_schema_version} does not match "
                f"expected version {EXPECTED_SCHEMA_VERSION}. "
                "Please run database migrations."
            )

        # --- License key validation (optional) ---
        if license_key:
            # You can add license validation logic here
            # For now, just log it
            logger.info(f"License key provided: {license_key[:8]}...")

        # Calculate response time
        end_time = time.time()
        ping_ms = int((end_time - start_time) * 1000)
        response_data['ping_ms'] = ping_ms

        return Response(response_data, status=status.HTTP_200_OK)


# ----------------------------------------------------------------------
# Version Compatibility Helper
# ----------------------------------------------------------------------

def _is_version_compatible(client_version: str, min_version: str) -> bool:
    """
    Check if client version is compatible with minimum required version.
    
    Args:
        client_version: Client version string (e.g., "1.2.3")
        min_version: Minimum required version string (e.g., "1.0.0")
    
    Returns:
        bool: True if client version is >= minimum version
    """
    try:
        def parse_version(version_str: str) -> tuple:
            """Parse version string into tuple of integers."""
            parts = version_str.split('.')
            # Pad with zeros to ensure we have at least 3 parts
            while len(parts) < 3:
                parts.append('0')
            return tuple(int(p) for p in parts[:3])
        
        client_parts = parse_version(client_version)
        min_parts = parse_version(min_version)
        
        return client_parts >= min_parts
    except (ValueError, AttributeError):
        # If version parsing fails, assume compatible
        logger.warning(f"Failed to parse versions: client={client_version}, min={min_version}")
        return True