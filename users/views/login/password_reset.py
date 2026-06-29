# users/views/password_reset.py (Password Change)
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from notifications.services.base import NotificationService
from notifications.services.event import NotificationEventService
from users.models.security_log import SecurityLog
from users.models.user_security_settings import UserSecuritySettings
from users.utils.authentications import IsAuthenticatedAndNotBlacklisted
from utils.response import CustomPagination, _success, _error
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
# Request/Response serializers for documentation
# ----------------------------------------------------------------------

class PasswordChangeRequestSerializer(serializers.Serializer):
    """Request serializer for password change."""
    current_password = serializers.CharField(required=True, write_only=True, help_text="Current password")
    new_password = serializers.CharField(required=True, write_only=True, min_length=8, help_text="New password")
    confirm_password = serializers.CharField(required=True, write_only=True, help_text="Confirm new password")


class PasswordChangeResponseDataSerializer(serializers.Serializer):
    """Response data for password change."""
    message = serializers.CharField()
    changed_at = serializers.DateTimeField()


class PasswordChangeSuccessResponseSerializer(serializers.Serializer):
    """Full response for successful password change."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordChangeResponseDataSerializer()


class PasswordChangeErrorResponseSerializer(serializers.Serializer):
    """Error response for password change."""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()
    errors = serializers.ListField(child=serializers.CharField(), required=False)


class PasswordStrengthCheckRequestSerializer(serializers.Serializer):
    """Request serializer for password strength check."""
    password = serializers.CharField(required=True, help_text="Password to check")


class PasswordStrengthCheckResponseDataSerializer(serializers.Serializer):
    """Response data for password strength check."""
    strength_score = serializers.IntegerField()
    strength_level = serializers.CharField()
    is_acceptable = serializers.BooleanField()
    errors = serializers.ListField(child=serializers.CharField())
    suggestions = serializers.ListField(child=serializers.CharField())


class PasswordStrengthCheckResponseSerializer(serializers.Serializer):
    """Full response for password strength check."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordStrengthCheckResponseDataSerializer()


class PasswordHistoryEventSerializer(serializers.Serializer):
    """Individual password history event."""
    event_type = serializers.CharField()
    created_at = serializers.DateTimeField()
    ip_address = serializers.CharField(allow_null=True)
    user_agent = serializers.CharField(allow_null=True)
    success = serializers.BooleanField()


class PasswordHistoryResponseDataSerializer(serializers.Serializer):
    """Response data for password history."""
    total_events = serializers.IntegerField()
    events = PasswordHistoryEventSerializer(many=True)


class PasswordHistoryResponseSerializer(serializers.Serializer):
    """Full response for password history."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordHistoryResponseDataSerializer()


# ----------------------------------------------------------------------
# Password Change View
# ----------------------------------------------------------------------

class PasswordChangeView(APIView):
    """
    Change password for authenticated users.
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]

    @extend_schema(
        tags=["Password Management"],
        request=PasswordChangeRequestSerializer,
        responses={
            200: PasswordChangeSuccessResponseSerializer,
            400: PasswordChangeErrorResponseSerializer,
            401: PasswordChangeErrorResponseSerializer,
            500: PasswordChangeErrorResponseSerializer,
        },
        description=(
            "Change the authenticated user's password. "
            "Requires current password for verification. "
            "New password must meet strength requirements."
        ),
        examples=[
            OpenApiExample(
                "Change password request",
                value={
                    "current_password": "OldSecurePass123!",
                    "new_password": "NewSecurePass456!",
                    "confirm_password": "NewSecurePass456!",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": True,
                    "message": "Password changed successfully",
                    "data": {
                        "message": "Password changed successfully",
                        "changed_at": "2025-01-02T10:00:00Z"
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Current password incorrect",
                value={"status": False, "detail": "Current password is incorrect"},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Passwords do not match",
                value={"status": False, "detail": "New passwords do not match"},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Password too weak",
                value={
                    "status": False,
                    "detail": "Password does not meet requirements",
                    "errors": [
                        "Password must be at least 8 characters long",
                        "Password must contain at least one number",
                    ]
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        current_password = request.data.get("current_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        # Validate required fields
        if not all([current_password, new_password, confirm_password]):
            return _error(
                data={"detail": "Current password, new password and confirmation are required"},
                message="Current password, new password and confirmation are required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if passwords match
        if new_password != confirm_password:
            return _error(
                data={"detail": "New passwords do not match"},
                message="New passwords do not match",
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if new password is different from current password
        if current_password == new_password:
            return _error(
                data={"detail": "New password must be different from current password"},
                message="New password must be different from current password",
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        # Verify current password
        if not check_password(current_password, user.password):
            transaction.set_rollback(True)

            log_audit_event(
                request=request,
                user=user,
                action_type="PASSWORD_CHANGE_FAILED",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": "Incorrect current password"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            SecurityLog.objects.create(
                user=user,
                event_type="password_change_failed",
                ip_address=client_ip,
                user_agent=user_agent,
                details="Incorrect current password provided",
            )

            return _error(
                data={"detail": "Current password is incorrect"},
                message="Current password is incorrect",
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate password strength
        password_errors = self._validate_password_strength(new_password)
        if password_errors:
            transaction.set_rollback(True)
            return _error(
                data={
                    "detail": "Password does not meet requirements",
                    "errors": password_errors,
                },
                message="Password does not meet requirements",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Update password
            user.password = make_password(new_password)
            user.save()

            # Log successful password change
            log_audit_event(
                request=request,
                user=user,
                action_type="PASSWORD_CHANGE",
                model_name="User",
                object_id=str(user.id),
                changes={
                    "detail": "Password changed successfully",
                    "password_changed_at": timezone.now().isoformat(),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            SecurityLog.objects.create(
                user=user,
                event_type="password_changed",
                ip_address=client_ip,
                user_agent=user_agent,
                details="Password changed successfully",
            )

            # Send notification if enabled
            security_settings = UserSecuritySettings.objects.get(user=user)
            if security_settings.alert_on_password_change:
                try:
                    NotificationEventService.send_password_changed(user)
                except Exception as e:
                    logger.warning(f"Failed to send password change notification: {e}")

            # Invalidate other sessions (optional)
            self._invalidate_other_sessions(user, request.session.session_key)

            return _success(
                data={
                    "message": "Password changed successfully",
                    "changed_at": timezone.now().isoformat(),
                },
                message="Password changed successfully",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Password change failed for user {user.id}: {exc}")

            log_audit_event(
                request=request,
                user=user,
                action_type="PASSWORD_CHANGE_FAILED",
                model_name="User",
                object_id=str(user.id),
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": "An error occurred while changing password"},
                message="An error occurred while changing password",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _validate_password_strength(self, password):
        """Validate password strength."""
        errors = []

        if len(password) < 8:
            errors.append("Password must be at least 8 characters long")

        if not any(char.isdigit() for char in password):
            errors.append("Password must contain at least one number")

        if not any(char.isupper() for char in password):
            errors.append("Password must contain at least one uppercase letter")

        if not any(char.islower() for char in password):
            errors.append("Password must contain at least one lowercase letter")

        if not any(char in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for char in password):
            errors.append("Password must contain at least one special character")

        # Check for common passwords
        common_passwords = ["password", "12345678", "qwerty", "admin", "letmein"]
        if password.lower() in common_passwords:
            errors.append("Password is too common")

        return errors

    def _invalidate_other_sessions(self, user, current_session_key):
        """Invalidate all other sessions except the current one."""
        try:
            from django.contrib.sessions.models import Session
            from django.contrib.auth import SESSION_KEY

            sessions = Session.objects.filter(expire_date__gte=timezone.now()).exclude(
                session_key=current_session_key
            )

            for session in sessions:
                session_data = session.get_decoded()
                if session_data.get(SESSION_KEY) == str(user.id):
                    session.delete()

        except Exception as e:
            logger.warning(f"Could not invalidate other sessions: {e}")


# ----------------------------------------------------------------------
# Password Strength Check View
# ----------------------------------------------------------------------

class PasswordStrengthCheckView(APIView):
    """
    Check password strength without changing password.
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]

    @extend_schema(
        tags=["Password Management"],
        request=PasswordStrengthCheckRequestSerializer,
        responses={
            200: PasswordStrengthCheckResponseSerializer,
            400: PasswordChangeErrorResponseSerializer,
            401: PasswordChangeErrorResponseSerializer,
        },
        description=(
            "Check the strength of a password without changing it. "
            "Returns a score, strength level, and suggestions for improvement."
        ),
        examples=[
            OpenApiExample(
                "Check password strength request",
                value={"password": "MySecurePass123!"},
                request_only=True,
            ),
            OpenApiExample(
                "Strong password response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "strength_score": 85,
                        "strength_level": "strong",
                        "is_acceptable": True,
                        "errors": [],
                        "suggestions": []
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Weak password response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "strength_score": 25,
                        "strength_level": "weak",
                        "is_acceptable": False,
                        "errors": [
                            "Password must be at least 8 characters long",
                            "Password must contain at least one number",
                        ],
                        "suggestions": [
                            "Use at least 12 characters for better security",
                            "Add special characters",
                        ]
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def post(self, request):
        password = request.data.get("password", "")

        if not password:
            return _error(
                data={"detail": "Password is required"},
                message="Password is required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        errors = self._validate_password_strength(password)
        strength_score = self._calculate_password_strength(password)

        return _success(
            data={
                "strength_score": strength_score,
                "strength_level": self._get_strength_level(strength_score),
                "is_acceptable": len(errors) == 0,
                "errors": errors,
                "suggestions": self._get_password_suggestions(password),
            },
            message="Success",
            status=status.HTTP_200_OK,
        )

    def _validate_password_strength(self, password):
        """Same validation as PasswordChangeView."""
        errors = []

        if len(password) < 8:
            errors.append("Password must be at least 8 characters long")

        if not any(char.isdigit() for char in password):
            errors.append("Password must contain at least one number")

        if not any(char.isupper() for char in password):
            errors.append("Password must contain at least one uppercase letter")

        if not any(char.islower() for char in password):
            errors.append("Password must contain at least one lowercase letter")

        if not any(char in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for char in password):
            errors.append("Password must contain at least one special character")

        return errors

    def _calculate_password_strength(self, password):
        """Calculate password strength score (0-100)."""
        score = 0

        # Length (max 40 points)
        score += min(len(password) * 4, 40)

        # Character variety
        if any(char.isdigit() for char in password):
            score += 10
        if any(char.isupper() for char in password):
            score += 10
        if any(char.islower() for char in password):
            score += 10
        if any(char in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for char in password):
            score += 20

        # Deductions for common patterns
        common_patterns = ["123", "abc", "qwe", "password", "admin"]
        for pattern in common_patterns:
            if pattern in password.lower():
                score -= 15

        return max(0, min(100, score))

    def _get_strength_level(self, score):
        """Get strength level based on score."""
        if score >= 80:
            return "strong"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "fair"
        else:
            return "weak"

    def _get_password_suggestions(self, password):
        """Get suggestions for improving password strength."""
        suggestions = []

        if len(password) < 12:
            suggestions.append("Use at least 12 characters for better security")

        if not any(char in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for char in password):
            suggestions.append("Add special characters")

        if password.isalnum():
            suggestions.append("Mix letters, numbers and special characters")

        # Check for sequential characters
        for i in range(len(password) - 2):
            if password[i:i+3].isdigit():
                num = int(password[i:i+3])
                if num in range(100, 1000) and num % 111 == 0:
                    suggestions.append("Avoid sequential numbers (e.g., 123, 456)")
                    break

        return suggestions


# ----------------------------------------------------------------------
# Password History View
# ----------------------------------------------------------------------

class PasswordHistoryView(APIView):
    """
    Get user's password change history.
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Password Management"],
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
            200: PasswordHistoryResponseSerializer,
            401: PasswordChangeErrorResponseSerializer,
            403: PasswordChangeErrorResponseSerializer,
            500: PasswordChangeErrorResponseSerializer,
        },
        description=(
            "Retrieve the user's password change history. "
            "Includes both successful changes and failed attempts."
        ),
        examples=[
            OpenApiExample(
                "Password history response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "total_events": 5,
                        "events": [
                            {
                                "event_type": "password_changed",
                                "created_at": "2025-01-02T10:00:00Z",
                                "ip_address": "192.168.1.100",
                                "user_agent": "Mozilla/5.0...",
                                "success": True,
                            },
                            {
                                "event_type": "password_change_failed",
                                "created_at": "2025-01-01T09:00:00Z",
                                "ip_address": "192.168.1.100",
                                "user_agent": "Mozilla/5.0...",
                                "success": False,
                            }
                        ]
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request):
        try:
            user = request.user

            password_events = SecurityLog.objects.filter(
                user=user,
                event_type__in=["password_changed", "password_change_failed"]
            ).order_by("-created_at")

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(password_events, request)

            events_data = []
            for event in page:
                events_data.append({
                    "event_type": event.event_type,
                    "created_at": event.created_at,
                    "ip_address": event.ip_address,
                    "user_agent": event.user_agent,
                    "success": event.event_type == "password_changed",
                })

            return paginator.get_paginated_response(
                data=events_data,
                message="Password history retrieved successfully."
            )

        except Exception as e:
            logger.exception(f"Error retrieving password history for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve password history.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )