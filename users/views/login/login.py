# users/views/login.py
import uuid
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken

from audit.utils.log import log_audit_event
from users.enums.base import UserStatus
from users.models import User
from users.models.login_checkpoint import LoginCheckpoint
from users.models.login_session import LoginSession
from users.models.otp_request import OtpRequest
from users.models.security_log import SecurityLog
from users.models.user_security_settings import UserSecuritySettings
from users.serializers.User import UserReadSerializer
from utils.security import get_client_ip
from utils.response import _success, _error

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes

User = get_user_model()
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Request/Response serializers for documentation
# ----------------------------------------------------------------------

class LoginRequestSerializer(serializers.Serializer):
    """Request serializer for login."""
    email = serializers.CharField(required=True, help_text="Email or username")
    password = serializers.CharField(required=True, write_only=True, help_text="Password")


class LoginSuccessResponseDataSerializer(serializers.Serializer):
    """Response data for successful login."""
    user = UserReadSerializer()
    refreshToken = serializers.CharField()
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField()
    message = serializers.CharField()


class LoginSuccessResponseSerializer(serializers.Serializer):
    """Full response for successful login."""
    status = serializers.BooleanField()
    user = UserReadSerializer()
    refreshToken = serializers.CharField()
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField()
    message = serializers.CharField()


class Login2FAInitResponseDataSerializer(serializers.Serializer):
    """Response data when 2FA is required."""
    requires_2fa = serializers.BooleanField()
    checkpoint_token = serializers.CharField()
    message = serializers.CharField()
    expires_in = serializers.IntegerField()


class Login2FAInitResponseSerializer(serializers.Serializer):
    """Full response for 2FA initiation."""
    status = serializers.BooleanField()
    requires_2fa = serializers.BooleanField()
    checkpoint_token = serializers.CharField()
    message = serializers.CharField()
    expires_in = serializers.IntegerField()


class LoginErrorResponseSerializer(serializers.Serializer):
    """Error response for login."""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


class Verify2FARequestSerializer(serializers.Serializer):
    """Request serializer for 2FA verification."""
    checkpoint_token = serializers.CharField(required=True)
    otp_code = serializers.CharField(max_length=6, min_length=6, required=True)


class Verify2FASuccessResponseDataSerializer(serializers.Serializer):
    """Response data for successful 2FA login."""
    user = UserReadSerializer()
    refreshToken = serializers.CharField()
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField()
    message = serializers.CharField()


class Verify2FASuccessResponseSerializer(serializers.Serializer):
    """Full response for successful 2FA login."""
    status = serializers.BooleanField()
    user = UserReadSerializer()
    refreshToken = serializers.CharField()
    accessToken = serializers.CharField()
    expiresIn = serializers.IntegerField()
    message = serializers.CharField()


class Resend2FARequestSerializer(serializers.Serializer):
    """Request serializer for resending 2FA OTP."""
    checkpoint_token = serializers.CharField(required=True)


class Resend2FASuccessResponseDataSerializer(serializers.Serializer):
    """Response data for resending 2FA OTP."""
    message = serializers.CharField()
    expires_in = serializers.IntegerField()


class Resend2FASuccessResponseSerializer(serializers.Serializer):
    """Full response for resending 2FA OTP."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    expires_in = serializers.IntegerField()


# ----------------------------------------------------------------------
# Login View
# ----------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class LoginView(APIView):
    """
    Login endpoint. Supports both standard login and 2FA‑enabled login.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=LoginRequestSerializer,
        responses={
            200: LoginSuccessResponseSerializer,
            200: Login2FAInitResponseSerializer,  # Different structure but same status
            400: LoginErrorResponseSerializer,
            401: LoginErrorResponseSerializer,
            404: LoginErrorResponseSerializer,
            500: LoginErrorResponseSerializer,
        },
        description=(
            "Authenticate user with email/username and password. "
            "If 2FA is enabled, returns a checkpoint_token for OTP verification. "
            "Otherwise, returns access and refresh tokens."
        ),
        examples=[
            OpenApiExample(
                "Standard login request",
                value={"email": "user@example.com", "password": "securepassword"},
                request_only=True,
            ),
            OpenApiExample(
                "Standard login response",
                value={
                    "status": True,
                    "user": {
                        "id": 1,
                        "username": "johndoe",
                        "email": "john@example.com",
                        "first_name": "John",
                        "last_name": "Doe",
                        "full_name": "John Doe",
                        "avatar": None,
                        "user_type": "staff",
                        "user_type_display": "Staff",
                        "status": "active",
                        "status_display": "Active",
                        "phone_number": "+1234567890",
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-01T00:00:00Z",
                        "security_settings": {
                            "id": 1,
                            "two_factor_enabled": False,
                            "recovery_email": None,
                            "recovery_phone": None,
                            "alert_on_new_device": True,
                            "alert_on_password_change": True,
                            "alert_on_failed_login": True,
                            "created_at": "2025-01-01T00:00:00Z",
                            "updated_at": "2025-01-01T00:00:00Z",
                        },
                        "is_restricted": False,
                        "is_suspended": False,
                        "is_admin": False,
                        "is_manager": False,
                    },
                    "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "expiresIn": 3600,
                    "message": "Login successful",
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "2FA required response",
                value={
                    "status": True,
                    "requires_2fa": True,
                    "checkpoint_token": "550e8400-e29b-41d4-a716-446655440000",
                    "message": "Two-factor authentication required. Please check your email for the verification code.",
                    "expires_in": 300,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid credentials",
                value={"status": False, "detail": "Invalid credentials"},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_name = user_agent[:100]

        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return _error(
                data={"detail": "Please provide both email and password"},
                message="Please provide both email and password",
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Authenticate user
        try:
            user = User.objects.get(Q(username=email) | Q(email=email))
        except User.DoesNotExist:
            log_audit_event(
                request=request,
                user=None,
                action_type="login_failed",
                model_name="User",
                object_id="unknown",
                changes={"error": "User not found", "email": email},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "No Account found."},
                message="No Account found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        if not user.check_password(password):
            log_audit_event(
                request=request,
                user=user,
                action_type="login_failed",
                model_name="User",
                object_id=str(user.id),
                changes={"error": "Invalid credentials", "email": email},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "Invalid credentials"},
                message="Invalid credentials",
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check account status
        if user.status != UserStatus.ACTIVE:
            return _error(
                data={"detail": f"Account status: {user.status}. Please contact administrator."},
                message=f"Account status: {user.status}",
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check 2FA
        security_settings, _ = UserSecuritySettings.objects.get_or_create(user=user)

        if security_settings.two_factor_enabled:
            return self._initiate_2fa(user, request, client_ip, user_agent, device_name)
        else:
            return self._complete_login(user, request, client_ip, user_agent, device_name)

    def _initiate_2fa(self, user, request, client_ip, user_agent, device_name):
        """Initiate 2FA process by sending OTP."""
        try:
            otp_code = f"{random.randint(0, 999999):06d}"

            checkpoint_token = uuid.uuid4()
            LoginCheckpoint.objects.create(
                user=user,
                email=user.email,
                token=checkpoint_token,
                expires_at=timezone.now() + timedelta(minutes=10),
            )

            OtpRequest.objects.create(
                user=user,
                otp_code=otp_code,
                type=OtpRequest.EMAIL,
                email=user.email,
                expires_at=timezone.now() + timedelta(minutes=5),
            )

            # TODO: Send OTP via email/SMS
            logger.info(f"2FA OTP for {user.email}: {otp_code}")

            log_audit_event(
                request=request,
                user=user,
                action_type="2fa_initiated",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": "2FA login initiated", "method": "email"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return Response(
                {
                    "status": True,
                    "requires_2fa": True,
                    "checkpoint_token": str(checkpoint_token),
                    "message": "Two-factor authentication required. Please check your email for the verification code.",
                    "expires_in": 300,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error initiating 2FA for user {user.id}: {str(e)}")
            return _error(
                data={"detail": "Error initiating two-factor authentication"},
                message="Error initiating two-factor authentication",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _complete_login(self, user, request, client_ip, user_agent, device_name):
        """Complete login by generating tokens."""
        try:
            user.last_login = timezone.now()
            user.save()

            refresh = RefreshToken.for_user(user)
            access_token = refresh.access_token
            access_exp = int(refresh.access_token.payload["exp"])

            refresh_jti = refresh["jti"]
            access_jti = access_token["jti"]

            lifetime = settings.SIMPLE_JWT.get(
                "REFRESH_TOKEN_LIFETIME", timezone.timedelta(days=7)
            )
            expires_at = timezone.now() + lifetime

            LoginSession.objects.create(
                id=uuid.uuid4(),
                user=user,
                device_name=device_name,
                ip_address=client_ip,
                expires_at=expires_at,
                refresh_token=refresh_jti,
                access_token=access_jti,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="login",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": "User logged in successfully"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            SecurityLog.objects.create(
                user=user,
                event_type="login",
                ip_address=client_ip,
                user_agent=user_agent,
                details="User logged in successfully",
            )

            user_data = UserReadSerializer(user, context={"request": request}).data

            return Response(
                {
                    "status": True,
                    "user": user_data,
                    "refreshToken": str(refresh),
                    "accessToken": str(access_token),
                    "expiresIn": access_exp,
                    "message": "Login successful",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error completing login for user {user.id}: {str(e)}")
            return _error(
                data={"detail": "Error completing login"},
                message="Error completing login",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Verify 2FA Login View
# ----------------------------------------------------------------------

class Verify2FALoginView(APIView):
    """
    Verify 2FA OTP and complete login.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=Verify2FARequestSerializer,
        responses={
            200: Verify2FASuccessResponseSerializer,
            400: LoginErrorResponseSerializer,
            401: LoginErrorResponseSerializer,
            500: LoginErrorResponseSerializer,
        },
        description="Verify the 2FA OTP using the checkpoint token and complete the login.",
        examples=[
            OpenApiExample(
                "Verify 2FA request",
                value={
                    "checkpoint_token": "550e8400-e29b-41d4-a716-446655440000",
                    "otp_code": "123456",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Verify 2FA success",
                value={
                    "status": True,
                    "user": {"username": "username"},
                    "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "expiresIn": 3600,
                    "message": "Login successful with two-factor authentication",
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid OTP",
                value={"status": False, "detail": "Invalid or expired OTP code"},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_name = user_agent[:100]

        checkpoint_token = request.data.get("checkpoint_token")
        otp_code = request.data.get("otp_code")

        if not checkpoint_token or not otp_code:
            return _error(
                data={"detail": "Checkpoint token and OTP code are required"},
                message="Checkpoint token and OTP code are required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Find valid checkpoint
            checkpoint = LoginCheckpoint.objects.filter(
                token=checkpoint_token, is_used=False, expires_at__gt=timezone.now()
            ).first()

            if not checkpoint:
                return _error(
                    data={"detail": "Invalid or expired checkpoint token"},
                    message="Invalid or expired checkpoint token",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = checkpoint.user

            # Find valid OTP
            otp = OtpRequest.objects.filter(
                user=user,
                otp_code=otp_code,
                type=OtpRequest.EMAIL,
                is_used=False,
                expires_at__gte=timezone.now(),
            ).first()

            if not otp:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type="2fa_failed",
                    model_name="User",
                    object_id=str(user.id),
                    changes={"error": "Invalid 2FA OTP"},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )
                return _error(
                    data={"detail": "Invalid or expired OTP code"},
                    message="Invalid or expired OTP code",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Mark OTP and checkpoint as used
            otp.is_used = True
            otp.save()

            checkpoint.is_used = True
            checkpoint.save()

            # Complete login
            user.last_login = timezone.now()
            user.save()

            refresh = RefreshToken.for_user(user)
            access_token = refresh.access_token
            access_exp = int(refresh.access_token.payload["exp"])

            refresh_jti = refresh["jti"]
            access_jti = access_token["jti"]

            lifetime = settings.SIMPLE_JWT.get(
                "REFRESH_TOKEN_LIFETIME", timezone.timedelta(days=7)
            )
            expires_at = timezone.now() + lifetime

            LoginSession.objects.create(
                id=uuid.uuid4(),
                user=user,
                device_name=device_name,
                ip_address=client_ip,
                expires_at=expires_at,
                refresh_token=refresh_jti,
                access_token=access_jti,
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="login_2fa",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": "User logged in successfully with 2FA"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            SecurityLog.objects.create(
                user=user,
                event_type="login",
                ip_address=client_ip,
                user_agent=user_agent,
                details="User logged in successfully with 2FA",
            )

            user_data = UserReadSerializer(user, context={"request": request}).data

            return Response(
                {
                    "status": True,
                    "user": user_data,
                    "refreshToken": str(refresh),
                    "accessToken": str(access_token),
                    "expiresIn": access_exp,
                    "message": "Login successful with two-factor authentication",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error verifying 2FA: {str(e)}")
            return _error(
                data={"detail": "Error verifying two-factor authentication"},
                message="Error verifying two-factor authentication",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Resend 2FA OTP View
# ----------------------------------------------------------------------

class Resend2FAOTPView(APIView):
    """
    Resend 2FA OTP code.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=Resend2FARequestSerializer,
        responses={
            200: Resend2FASuccessResponseSerializer,
            400: LoginErrorResponseSerializer,
            500: LoginErrorResponseSerializer,
        },
        description="Resend the 2FA OTP code to the user's email.",
        examples=[
            OpenApiExample(
                "Resend OTP request",
                value={"checkpoint_token": "550e8400-e29b-41d4-a716-446655440000"},
                request_only=True,
            ),
            OpenApiExample(
                "Resend OTP success",
                value={
                    "status": True,
                    "message": "Verification code has been resent",
                    "expires_in": 300,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid checkpoint",
                value={"status": False, "detail": "Invalid or expired checkpoint token"},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        checkpoint_token = request.data.get("checkpoint_token")

        if not checkpoint_token:
            return _error(
                data={"detail": "Checkpoint token is required"},
                message="Checkpoint token is required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Find valid checkpoint
            checkpoint = LoginCheckpoint.objects.filter(
                token=checkpoint_token, is_used=False, expires_at__gt=timezone.now()
            ).first()

            if not checkpoint:
                return _error(
                    data={"detail": "Invalid or expired checkpoint token"},
                    message="Invalid or expired checkpoint token",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = checkpoint.user

            # Generate new OTP
            otp_code = f"{random.randint(0, 999999):06d}"

            OtpRequest.objects.create(
                user=user,
                otp_code=otp_code,
                type=OtpRequest.EMAIL,
                email=user.email,
                expires_at=timezone.now() + timedelta(minutes=5),
            )

            # TODO: Send OTP via email/SMS
            logger.info(f"Resent 2FA OTP for {user.email}: {otp_code}")

            return Response(
                {
                    "status": True,
                    "message": "Verification code has been resent",
                    "expires_in": 300,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error resending 2FA OTP: {str(e)}")
            return _error(
                data={"detail": "Error resending verification code"},
                message="Error resending verification code",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )