# users/views/security_settings.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, serializers
from django.db import transaction
from django.shortcuts import get_object_or_404
import logging

from users.models.user_security_settings import UserSecuritySettings

from users.serializers.UserSecuritySettings.main import UserSecuritySettingsSerializer
from users.serializers.UserSecuritySettings.write import UserSecuritySettingsWriteSerializer
from users.utils.authentications import IsAuthenticatedAndNotBlacklisted
from utils.response import _success, _error

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class SecuritySettingsResponseDataSerializer(serializers.Serializer):
    """Response data for security settings."""
    data = UserSecuritySettingsSerializer()


class SecuritySettingsResponseSerializer(serializers.Serializer):
    """Full response for security settings."""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = UserSecuritySettingsSerializer()


class SecuritySettingsErrorResponseSerializer(serializers.Serializer):
    """Error response for security settings."""
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class UserSecuritySettingsAPIView(APIView):
    """
    GET  -> Get the current user's security settings
    PATCH -> Update the current user's security settings
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]

    @extend_schema(
        tags=["Security Settings"],
        responses={
            200: SecuritySettingsResponseSerializer,
            401: SecuritySettingsErrorResponseSerializer,
            403: SecuritySettingsErrorResponseSerializer,
            500: SecuritySettingsErrorResponseSerializer,
        },
        description="Retrieve the current user's security settings.",
    )
    def get(self, request):
        try:
            settings_obj, created = UserSecuritySettings.objects.get_or_create(user=request.user)
            serializer = UserSecuritySettingsSerializer(
                settings_obj,
                context={"request": request}
            )
            return _success(
                data=serializer.data,
                message="Security settings retrieved successfully.",
                status=status.HTTP_200_OK
            )
        except Exception as e:
            logger.exception("Error retrieving security settings")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(
        tags=["Security Settings"],
        request=UserSecuritySettingsWriteSerializer,
        responses={
            200: SecuritySettingsResponseSerializer,
            400: SecuritySettingsErrorResponseSerializer,
            401: SecuritySettingsErrorResponseSerializer,
            403: SecuritySettingsErrorResponseSerializer,
            500: SecuritySettingsErrorResponseSerializer,
        },
        description="Update the current user's security settings.",
        examples=[
            OpenApiExample(
                "Update security settings",
                value={
                    "two_factor_enabled": True,
                    "recovery_email": "backup@example.com",
                    "recovery_phone": "+639123456789",
                    "alert_on_new_device": True,
                    "alert_on_password_change": True,
                    "alert_on_failed_login": True,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Update response",
                value={
                    "status": True,
                    "message": "Security settings updated successfully.",
                    "data": {
                        "id": 1,
                        "user": 1,
                        "user_username": "admin",
                        "user_email": "admin@example.com",
                        "two_factor_enabled": True,
                        "recovery_email": "backup@example.com",
                        "recovery_phone": "+639123456789",
                        "alert_on_new_device": True,
                        "alert_on_password_change": True,
                        "alert_on_failed_login": True,
                        "updated_at": "2025-01-02T00:00:00Z",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    @transaction.atomic
    def patch(self, request):
        try:
            settings_obj, created = UserSecuritySettings.objects.get_or_create(user=request.user)
        except Exception as e:
            logger.exception("Error getting security settings")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        serializer = UserSecuritySettingsWriteSerializer(
            settings_obj,
            data=request.data,
            partial=True,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            updated_settings = serializer.save()

            read_serializer = UserSecuritySettingsSerializer(
                updated_settings,
                context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Security settings updated successfully.",
                status=status.HTTP_200_OK
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception("Error updating security settings")
            return _error(
                data={"detail": str(e)},
                message="Failed to update security settings.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )