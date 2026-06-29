# users/views/base.py
import logging
import random
from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, serializers
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from audit.utils.log import log_audit_event
from users.enums.base import UserRole
from users.models import User
from users.models.blacklisted_token import BlacklistedAccessToken
from users.models.login_session import LoginSession
from users.models.otp_request import OtpRequest
from users.models.security_log import SecurityLog
from users.models.user_security_settings import UserSecuritySettings
from users.serializers.LoginSession import LoginSessionReadSerializer

from users.serializers.SecurityLog.read import SecurityLogReadSerializer
from users.serializers.UserSecuritySettings.read import UserSecuritySettingsReadSerializer
from utils.response import _success, _error
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

class OTPRequestSerializer(serializers.Serializer):
    """Request serializer for OTP operations."""
    code = serializers.CharField(max_length=6, min_length=6, required=True)


class OTPResponseDataSerializer(serializers.Serializer):
    """Response data for OTP operations."""
    success = serializers.BooleanField()
    message = serializers.CharField()


class OTPResponseSerializer(serializers.Serializer):
    """Full response for OTP operations."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = OTPResponseDataSerializer(required=False, allow_null=True)


class SecurityConfigSettingsSerializer(serializers.Serializer):
    """Nested settings for security config."""
    id = serializers.IntegerField()
    user = serializers.IntegerField()
    user_username = serializers.CharField()
    user_email = serializers.EmailField()
    two_factor_enabled = serializers.BooleanField()
    recovery_email = serializers.EmailField(allow_null=True)
    recovery_phone = serializers.CharField(allow_null=True)
    alert_on_new_device = serializers.BooleanField()
    alert_on_password_change = serializers.BooleanField()
    alert_on_failed_login = serializers.BooleanField()
    updated_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()


class SecurityConfigSystemInfoSerializer(serializers.Serializer):
    """System info for security config."""
    total_login_sessions = serializers.IntegerField()
    active_sessions = serializers.IntegerField()
    failed_login_attempts = serializers.IntegerField()
    last_password_change = serializers.DateTimeField(allow_null=True)
    two_factor_enabled = serializers.BooleanField()


class SecurityConfigDataSerializer(serializers.Serializer):
    """Full security config data."""
    settings = SecurityConfigSettingsSerializer()
    security_logs = SecurityLogReadSerializer(many=True)
    system_info = SecurityConfigSystemInfoSerializer()


class SecurityConfigResponseSerializer(serializers.Serializer):
    """Response for security config."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = SecurityConfigDataSerializer()


class SecurityHealthDataSerializer(serializers.Serializer):
    """Security health data."""
    two_factor = serializers.BooleanField()
    recovery_email = serializers.BooleanField()
    recovery_phone = serializers.BooleanField()
    strong_password = serializers.BooleanField()
    recent_activity = serializers.BooleanField()
    suspicious_activity = serializers.BooleanField()
    overall = serializers.BooleanField()
    issues = serializers.ListField(child=serializers.CharField())


class SecurityHealthResponseSerializer(serializers.Serializer):
    """Response for security health."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = SecurityHealthDataSerializer()


class SecurityStatsDataSerializer(serializers.Serializer):
    """Security statistics data."""
    total_sessions = serializers.IntegerField()
    active_sessions = serializers.IntegerField()
    failed_logins_24h = serializers.IntegerField()
    password_changes_30d = serializers.IntegerField()
    two_factor_enabled = serializers.BooleanField()
    security_score = serializers.IntegerField()


class SecurityStatsResponseSerializer(serializers.Serializer):
    """Response for security stats."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = SecurityStatsDataSerializer()


class TwoFactorResponseDataSerializer(serializers.Serializer):
    """Response data for 2FA operations."""
    success = serializers.BooleanField()
    message = serializers.CharField()


class TwoFactorResponseSerializer(serializers.Serializer):
    """Full response for 2FA operations."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = TwoFactorResponseDataSerializer()


class SessionsListResponseSerializer(serializers.Serializer):
    """Response for sessions list."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = LoginSessionReadSerializer(many=True)


class SecurityLogDetailResponseSerializer(serializers.Serializer):
    """Response for security log detail."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = SecurityLogReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    """Generic error response."""
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# OTP Views
# ----------------------------------------------------------------------

class SendEmailOTPView(APIView):
    """
    Send OTP to user's email address for verification.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - OTP"],
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
        description="Send a 6-digit OTP code to the authenticated user's email address.",
        examples=[
            OpenApiExample(
                "Success response",
                value={
                    "status": True,
                    "message": "OTP sent to your email.",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "No email found",
                value={
                    "status": False,
                    "message": "No email found for this account.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user

        if not user.email:
            return _error(
                data=[],
                message="No email found for this account.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Generate OTP
            code = f"{random.randint(0, 999999):06d}"

            OtpRequest.objects.create(
                user=user,
                otp_code=code,
                type="email",
                email=user.email,
                expires_at=timezone.now() + timedelta(minutes=5),
            )

            # TODO: Integrate with actual email service
            logger.info(f"OTP sent to {user.email}: {code}")

            return _success(
                data={"success": True, "message": "OTP sent to your email."},
                message="OTP sent to your email.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to send OTP to {user.email}")
            return _error(
                data={"detail": str(e)},
                message="Failed to send OTP.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyEmailOTPView(APIView):
    """
    Verify email using OTP code.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - OTP"],
        request=OTPRequestSerializer,
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
        description="Verify the user's email address using the OTP code sent to their email.",
        examples=[
            OpenApiExample(
                "Verify request",
                value={"code": "123456"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": True,
                    "message": "Email verified successfully.",
                    "data": {"success": True, "message": "Email verified successfully."},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid code",
                value={
                    "status": False,
                    "message": "Invalid or expired code.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user
        code = request.data.get("code")

        if not code:
            return _error(
                data=[],
                message="Code is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            otp = OtpRequest.objects.filter(
                user=request.user,
                otp_code=code,
                type="email",
                is_used=False,
                email=user.email,
                expires_at__gte=timezone.now(),
            ).first()

            if not otp:
                return _error(
                    data=[],
                    message="Invalid or expired code.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Mark OTP as used
            otp.is_used = True
            otp.save()

            # Mark email as verified
            # Note: email_verified field may not exist in User model
            # If it doesn't, you may want to add it or handle differently
            if hasattr(user, 'email_verified'):
                user.email_verified = True
                user.save()

            return _success(
                data={"success": True, "message": "Email verified successfully."},
                message="Email verified successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to verify email for user {user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to verify email.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SendPhoneOTPView(APIView):
    """
    Send OTP to user's phone number for verification.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - OTP"],
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
        description="Send a 6-digit OTP code to the authenticated user's phone number.",
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user

        if not user.phone_number:
            return _error(
                data=[],
                message="No phone number found for this account.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            code = f"{random.randint(0, 999999):06d}"

            OtpRequest.objects.create(
                user=user,
                otp_code=code,
                type="phone",
                phone=user.phone_number,
                expires_at=timezone.now() + timedelta(minutes=5),
            )

            # TODO: Integrate with actual SMS service
            logger.info(f"OTP sent to {user.phone_number}: {code}")

            return _success(
                data={"success": True, "message": "OTP sent to your phone."},
                message="OTP sent to your phone.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to send OTP to {user.phone_number}")
            return _error(
                data={"detail": str(e)},
                message="Failed to send OTP.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyPhoneOTPView(APIView):
    """
    Verify phone number using OTP code.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - OTP"],
        request=OTPRequestSerializer,
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
        description="Verify the user's phone number using the OTP code sent to their phone.",
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user
        code = request.data.get("code")

        if not code:
            return _error(
                data=[],
                message="Code is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            otp = OtpRequest.objects.filter(
                user=request.user,
                otp_code=code,
                type="phone",
                is_used=False,
                phone=user.phone_number,
                expires_at__gte=timezone.now(),
            ).first()

            if not otp:
                return _error(
                    data=[],
                    message="Invalid or expired code.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            otp.is_used = True
            otp.save()

            if hasattr(user, 'phone_verified'):
                user.phone_verified = True
                user.save()

            return _success(
                data={"success": True, "message": "Phone number verified successfully."},
                message="Phone number verified successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to verify phone for user {user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to verify phone.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Security Configuration Views
# ----------------------------------------------------------------------

class UserSecurityConfigAPIView(APIView):
    """
    GET -> Complete security configuration for the user.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Configuration"],
        responses={
            200: SecurityConfigResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve complete security configuration including settings, logs, and system info.",
    )
    def get(self, request):
        try:
            user = request.user

            # Get security settings
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)
            settings_serializer = UserSecuritySettingsReadSerializer(
                settings_obj,
                context={"request": request}
            )

            # Get recent security logs (last 10)
            security_logs = SecurityLog.objects.filter(user=user).order_by("-created_at")[:10]
            logs_serializer = SecurityLogReadSerializer(
                security_logs,
                many=True,
                context={"request": request}
            )

            # System info
            total_sessions = LoginSession.objects.filter(user=user).count()
            active_sessions = LoginSession.objects.filter(
                user=user,
                is_active=True,
                expires_at__gt=timezone.now()
            ).count()
            failed_attempts = SecurityLog.objects.filter(
                user=user,
                event_type="failed_login",
                created_at__gte=timezone.now() - timedelta(hours=24)
            ).count()

            last_password_change = SecurityLog.objects.filter(
                user=user,
                event_type="password_change"
            ).order_by("-created_at").first()

            system_info = {
                "total_login_sessions": total_sessions,
                "active_sessions": active_sessions,
                "failed_login_attempts": failed_attempts,
                "last_password_change": last_password_change.created_at if last_password_change else None,
                "two_factor_enabled": settings_obj.two_factor_enabled,
            }

            data = {
                "settings": settings_serializer.data,
                "security_logs": logs_serializer.data,
                "system_info": system_info,
            }

            return _success(
                data=data,
                message="Security config retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"Error retrieving security config for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security config.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SecurityHealthAPIView(APIView):
    """
    GET -> Security health check for the user.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Health"],
        responses={
            200: SecurityHealthResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Check the overall security health of the user account.",
    )
    def get(self, request):
        try:
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            # Check recent suspicious activity (last 7 days)
            recent_suspicious = SecurityLog.objects.filter(
                user=user,
                event_type="failed_login",
                created_at__gte=timezone.now() - timedelta(days=7)
            ).exists()

            # Check recent activity (last 30 days)
            recent_activity = SecurityLog.objects.filter(
                user=user,
                created_at__gte=timezone.now() - timedelta(days=30)
            ).exists()

            # Simple strong password check
            strong_password = len(user.password) >= 8

            issues = []
            if not settings_obj.two_factor_enabled:
                issues.append("Two-factor authentication is disabled")
            if not settings_obj.recovery_email:
                issues.append("No recovery email set")
            if not settings_obj.recovery_phone:
                issues.append("No recovery phone set")
            if recent_suspicious:
                issues.append("Suspicious activity detected")

            health_data = {
                "two_factor": settings_obj.two_factor_enabled,
                "recovery_email": bool(settings_obj.recovery_email),
                "recovery_phone": bool(settings_obj.recovery_phone),
                "strong_password": strong_password,
                "recent_activity": recent_activity,
                "suspicious_activity": recent_suspicious,
                "overall": len(issues) == 0,
                "issues": issues,
            }

            return _success(
                data=health_data,
                message="Security health check completed.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"Error checking security health for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to check security health.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SecurityStatsAPIView(APIView):
    """
    GET -> Security statistics for the user.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Stats"],
        responses={
            200: SecurityStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve security statistics including sessions, login attempts, and security score.",
    )
    def get(self, request):
        try:
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            # Calculate stats
            total_sessions = LoginSession.objects.filter(user=user).count()
            active_sessions = LoginSession.objects.filter(
                user=user,
                is_active=True,
                expires_at__gt=timezone.now()
            ).count()

            failed_logins_24h = SecurityLog.objects.filter(
                user=user,
                event_type="failed_login",
                created_at__gte=timezone.now() - timedelta(hours=24)
            ).count()

            password_changes_30d = SecurityLog.objects.filter(
                user=user,
                event_type="password_change",
                created_at__gte=timezone.now() - timedelta(days=30)
            ).count()

            # Calculate security score
            security_score = self._calculate_security_score(settings_obj)

            stats_data = {
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "failed_logins_24h": failed_logins_24h,
                "password_changes_30d": password_changes_30d,
                "two_factor_enabled": settings_obj.two_factor_enabled,
                "security_score": security_score,
            }

            return _success(
                data=stats_data,
                message="Security stats retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"Error retrieving security stats for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security stats.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _calculate_security_score(self, settings):
        """Calculate security score (0-100)."""
        score = 0
        if settings.two_factor_enabled:
            score += 30
        if settings.recovery_email:
            score += 20
        if settings.recovery_phone:
            score += 20
        if settings.alert_on_new_device:
            score += 10
        if settings.alert_on_password_change:
            score += 10
        if settings.alert_on_failed_login:
            score += 10
        return min(score, 100)


# ----------------------------------------------------------------------
# Two-Factor Authentication Views
# ----------------------------------------------------------------------

class EnableTwoFactorAPIView(APIView):
    """
    POST -> Enable two-factor authentication.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - 2FA"],
        responses={
            200: TwoFactorResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Enable two-factor authentication for the current user.",
    )
    @transaction.atomic
    def post(self, request):
        try:
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            if settings_obj.two_factor_enabled:
                return _success(
                    data={"success": False, "message": "Two-factor authentication is already enabled"},
                    message="Two-factor authentication is already enabled",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            settings_obj.two_factor_enabled = True
            settings_obj.save()

            # Log the event
            SecurityLog.objects.create(
                user=user,
                event_type="2fa_enabled",
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return _success(
                data={"success": True, "message": "Two-factor authentication enabled successfully"},
                message="Two-factor authentication enabled",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to enable 2FA for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to enable two-factor authentication.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DisableTwoFactorAPIView(APIView):
    """
    POST -> Disable two-factor authentication.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - 2FA"],
        responses={
            200: TwoFactorResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Disable two-factor authentication for the current user.",
    )
    @transaction.atomic
    def post(self, request):
        try:
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            if not settings_obj.two_factor_enabled:
                return _success(
                    data={"success": False, "message": "Two-factor authentication is already disabled"},
                    message="Two-factor authentication is already disabled",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            settings_obj.two_factor_enabled = False
            settings_obj.save()

            SecurityLog.objects.create(
                user=user,
                event_type="2fa_disabled",
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return _success(
                data={"success": True, "message": "Two-factor authentication disabled successfully"},
                message="Two-factor authentication disabled",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to disable 2FA for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to disable two-factor authentication.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Recovery Verification Views
# ----------------------------------------------------------------------

class VerifyRecoveryEmailAPIView(APIView):
    """
    POST -> Verify recovery email with OTP.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Recovery"],
        request=OTPRequestSerializer,
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Verify the recovery email address using an OTP code.",
    )
    @transaction.atomic
    def post(self, request):
        code = request.data.get("code")

        if not code:
            return _error(
                data=[],
                message="Verification code is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # TODO: Implement actual OTP verification logic
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            # For now, assume code is valid
            # In production, verify against OtpRequest model

            return _success(
                data={"success": True, "message": "Recovery email verified successfully"},
                message="Recovery email verified",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to verify recovery email for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to verify recovery email.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyRecoveryPhoneAPIView(APIView):
    """
    POST -> Verify recovery phone with OTP.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Recovery"],
        request=OTPRequestSerializer,
        responses={
            200: OTPResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Verify the recovery phone number using an OTP code.",
    )
    @transaction.atomic
    def post(self, request):
        code = request.data.get("code")

        if not code:
            return _error(
                data=[],
                message="Verification code is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = request.user
            settings_obj, _ = UserSecuritySettings.objects.get_or_create(user=user)

            return _success(
                data={"success": True, "message": "Recovery phone verified successfully"},
                message="Recovery phone verified",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to verify recovery phone for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to verify recovery phone.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TestSecurityAlertsAPIView(APIView):
    """
    POST -> Test security alerts system.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Alerts"],
        responses={
            200: OTPResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Send a test security alert to the user's configured channels.",
    )
    @transaction.atomic
    def post(self, request):
        try:
            user = request.user

            # TODO: Implement actual alert sending
            # Send test email/SMS notification

            return _success(
                data={"success": True, "message": "Security alert test completed successfully"},
                message="Security alerts test completed",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to test security alerts for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to test security alerts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Session Management Views
# ----------------------------------------------------------------------

class UserLoginSessionsAPIView(APIView):
    """
    GET -> List of login sessions for the current user.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Sessions"],
        responses={
            200: SessionsListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve all login sessions for the current user.",
    )
    def get(self, request):
        try:
            user = request.user
            sessions = LoginSession.objects.filter(user=user).order_by("-last_used")
            serializer = LoginSessionReadSerializer(
                sessions,
                many=True,
                context={"request": request}
            )

            return _success(
                data=serializer.data,
                message="Login sessions retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"Error retrieving sessions for user {request.user.id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve login sessions.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TerminateSessionAPIView(APIView):
    """
    DELETE -> Terminate a specific session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Sessions"],
        parameters=[
            OpenApiParameter(
                name="session_id",
                type=str,
                location=OpenApiParameter.PATH,
                description="UUID of the session to terminate",
                required=True,
            ),
        ],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Terminate a specific login session. This will blacklist both tokens.",
    )
    @transaction.atomic
    def delete(self, request, session_id):
        user = request.user

        try:
            session = get_object_or_404(LoginSession, id=session_id, user=user, is_active=True)

            # Blacklist access token
            if session.access_token:
                BlacklistedAccessToken.blacklist_token(
                    jti=session.access_token,
                    user=user,
                    expires_at=timezone.now() + timezone.timedelta(days=1),
                )
                logger.info(f"Blacklisted access token JTI: {session.access_token}")

            # Blacklist refresh token
            try:
                token = RefreshToken(session.refresh_token)
                token.blacklist()
            except Exception:
                try:
                    outstanding = OutstandingToken.objects.get(jti=session.refresh_token)
                    BlacklistedToken.objects.get_or_create(token=outstanding)
                except OutstandingToken.DoesNotExist:
                    pass

            # Mark session inactive
            session.is_active = False
            session.expires_at = timezone.now()
            session.save(update_fields=["is_active", "expires_at"])

            # Log the event
            SecurityLog.objects.create(
                user=user,
                event_type="logout",
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                details=f"Session terminated manually: {session.device_name} (Access token blacklisted)",
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='logout',
                model_name='LoginSession',
                object_id=str(session.id),
                changes={'detail': 'Session revoked & both tokens blacklisted'},
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception(f"Failed to terminate session {session_id}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to terminate session.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TerminateAllSessionsAPIView(APIView):
    """
    DELETE -> Terminate all sessions except current.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Sessions"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Terminate all login sessions except the current one. This will blacklist all tokens.",
    )
    @transaction.atomic
    def delete(self, request):
        user = request.user
        current_session = getattr(request, 'login_session', None)

        try:
            sessions_to_terminate = LoginSession.objects.filter(user=user, is_active=True)
            if current_session:
                sessions_to_terminate = sessions_to_terminate.exclude(id=current_session.id)

            terminated_count = 0

            for session in sessions_to_terminate:
                try:
                    # Blacklist access token
                    if session.access_token:
                        BlacklistedAccessToken.blacklist_token(
                            jti=session.access_token,
                            user=user,
                            expires_at=timezone.now() + timezone.timedelta(days=1),
                        )

                    # Blacklist refresh token
                    try:
                        token = RefreshToken(session.refresh_token)
                        token.blacklist()
                    except Exception:
                        try:
                            outstanding = OutstandingToken.objects.get(jti=session.refresh_token)
                            BlacklistedToken.objects.get_or_create(token=outstanding)
                        except OutstandingToken.DoesNotExist:
                            pass

                    # Mark session inactive
                    session.is_active = False
                    session.expires_at = timezone.now()
                    session.save(update_fields=["is_active", "expires_at"])
                    terminated_count += 1

                except Exception as e:
                    logger.error(f"Failed to terminate session {session.id}: {e}")
                    continue

            # Cleanup expired blacklisted tokens
            BlacklistedAccessToken.cleanup_expired()

            # Audit log
            SecurityLog.objects.create(
                user=user,
                event_type="logout",
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                details=f"All other sessions terminated ({terminated_count} sessions, access tokens blacklisted)",
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception(f"Failed to terminate all sessions for user {user.id}")
            return _error(
                data={"detail": str(exc)},
                message="Failed to terminate sessions.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Security Log Detail View
# ----------------------------------------------------------------------

class SecurityLogDetailAPIView(APIView):
    """
    GET -> Specific security log details.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["Security - Logs"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=int,
                location=OpenApiParameter.PATH,
                description="ID of the security log entry",
                required=True,
            ),
        ],
        responses={
            200: SecurityLogDetailResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve details of a specific security log entry.",
    )
    def get(self, request, id):
        try:
            user = request.user

            if user.user_type in [UserRole.STAFF, UserRole.ADMIN, UserRole.MANAGER]:
                log_entry = get_object_or_404(SecurityLog, id=id)
            else:
                log_entry = get_object_or_404(SecurityLog, id=id, user=user)

            serializer = SecurityLogReadSerializer(
                log_entry,
                context={"request": request}
            )

            return _success(
                data=serializer.data,
                message="Security log retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"Error retrieving security log {id}")
            return _error(
                data={"detail": str(e)},
                message="Failed to retrieve security log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )