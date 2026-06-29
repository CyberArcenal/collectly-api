# users/views/password_recover.py
import logging
import random
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.core.mail import send_mail
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny

from audit.utils.log import log_audit_event
from notifications.utils.email import get_dynamic_email_backend
from users.models import User
from users.models.login_checkpoint import LoginCheckpoint
from users.models.otp_request import OtpRequest
from users.serializers.User import UserNestedSerializer
from utils.security import get_client_ip
from utils.response import _success, _error

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

class PasswordResetRequestSerializer(serializers.Serializer):
    """Request serializer for password reset request."""
    email = serializers.EmailField(required=True, help_text="Email address of the user")


class PasswordResetRequestResponseDataSerializer(serializers.Serializer):
    """Response data for password reset request."""
    message = serializers.CharField()


class PasswordResetRequestResponseSerializer(serializers.Serializer):
    """Full response for password reset request."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordResetRequestResponseDataSerializer(required=False, allow_null=True)


class PasswordResetVerifySerializer(serializers.Serializer):
    """Request serializer for password reset OTP verification."""
    email = serializers.EmailField(required=True, help_text="Email address")
    otp_code = serializers.CharField(max_length=6, min_length=6, required=True, help_text="6-digit OTP code")


class PasswordResetVerifyResponseDataSerializer(serializers.Serializer):
    """Response data for password reset OTP verification."""
    message = serializers.CharField()
    email = serializers.EmailField()
    verified = serializers.BooleanField()
    checkpoint_token = serializers.CharField()


class PasswordResetVerifyResponseSerializer(serializers.Serializer):
    """Full response for password reset OTP verification."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordResetVerifyResponseDataSerializer()


class PasswordResetCompleteSerializer(serializers.Serializer):
    """Request serializer for password reset completion."""
    checkpoint_token = serializers.CharField(required=True, help_text="Checkpoint token from verification")
    new_password = serializers.CharField(required=True, write_only=True, min_length=8, help_text="New password")
    confirm_password = serializers.CharField(required=True, write_only=True, help_text="Confirm new password")


class PasswordResetCompleteResponseDataSerializer(serializers.Serializer):
    """Response data for password reset completion."""
    message = serializers.CharField()


class PasswordResetCompleteResponseSerializer(serializers.Serializer):
    """Full response for password reset completion."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = PasswordResetCompleteResponseDataSerializer(required=False, allow_null=True)


class PasswordResetErrorResponseSerializer(serializers.Serializer):
    """Error response for password reset endpoints."""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


# ----------------------------------------------------------------------
# Password Reset Request View
# ----------------------------------------------------------------------

class PasswordResetRequestView(APIView):
    """
    Request a password reset OTP.
    Sends a 6-digit OTP to the user's email.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Password Reset"],
        request=PasswordResetRequestSerializer,
        responses={
            200: PasswordResetRequestResponseSerializer,
            400: PasswordResetErrorResponseSerializer,
            500: PasswordResetErrorResponseSerializer,
        },
        description=(
            "Request a password reset OTP. If the email exists, an OTP will be sent. "
            "For security reasons, the response is the same whether the email exists or not."
        ),
        examples=[
            OpenApiExample(
                "Request password reset",
                value={"email": "user@example.com"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": True,
                    "message": "If the email exists, a password reset OTP has been sent",
                    "data": {"message": "If the email exists, a password reset OTP has been sent"}
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing email",
                value={"status": False, "detail": "Email is required"},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        email = request.data.get("email")
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not email:
            return _error(
                data={"detail": "Email is required"},
                message="Email is required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Generic response for security (prevents email enumeration)
            logger.info(f"Password reset requested for non-existent email: {email}")
            return _success(
                data={"message": "If the email exists, a password reset OTP has been sent"},
                message="If the email exists, a password reset OTP has been sent",
                status=status.HTTP_200_OK,
            )

        if not user.is_active:
            return _error(
                data={"detail": "Account is deactivated"},
                message="Account is deactivated",
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check existing OTP
        existing = (
            OtpRequest.objects.filter(email=email, user=user)
            .order_by("-created_at")
            .first()
        )
        now = timezone.now()

        if existing and not existing.is_used and existing.expires_at > now:
            # Still valid, don't create new
            logger.info(f"Existing valid OTP found for {email}")
            return _success(
                data={"message": "If the email exists, a password reset OTP has been sent"},
                message="If the email exists, a password reset OTP has been sent",
                status=status.HTTP_200_OK,
            )

        try:
            # Generate new OTP
            otp_code = str(secrets.randbelow(900000) + 100000)
            expires_at = now + timedelta(minutes=15)

            OtpRequest.objects.create(
                email=email,
                user=user,
                otp_code=otp_code,
                expires_at=expires_at,
                is_used=False,
                attempt_count=0,
            )

            # TODO: Integrate with actual email service
            logger.info(f"Password reset OTP sent to {email}: {otp_code}")

            log_audit_event(
                request=request,
                user=user,
                action_type="password_reset_requested",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": f"Password reset requested for {email}"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"message": "If the email exists, a password reset OTP has been sent"},
                message="If the email exists, a password reset OTP has been sent",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to send password reset OTP to {email}")
            return _error(
                data={"detail": "Failed to send password reset OTP"},
                message="Failed to send password reset OTP",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Password Reset Verify View
# ----------------------------------------------------------------------

class PasswordResetVerifyView(APIView):
    """
    Verify the OTP for password reset.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Password Reset"],
        request=PasswordResetVerifySerializer,
        responses={
            200: PasswordResetVerifyResponseSerializer,
            400: PasswordResetErrorResponseSerializer,
            500: PasswordResetErrorResponseSerializer,
        },
        description=(
            "Verify the OTP code sent to the user's email. "
            "On successful verification, returns a checkpoint token for password reset completion."
        ),
        examples=[
            OpenApiExample(
                "Verify OTP request",
                value={"email": "user@example.com", "otp_code": "123456"},
                request_only=True,
            ),
            OpenApiExample(
                "Verify OTP success",
                value={
                    "status": True,
                    "message": "OTP verified successfully",
                    "data": {
                        "message": "OTP verified successfully",
                        "email": "user@example.com",
                        "verified": True,
                        "checkpoint_token": "550e8400-e29b-41d4-a716-446655440000"
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid OTP",
                value={"status": False, "detail": "Invalid OTP code"},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Expired OTP",
                value={"status": False, "detail": "OTP has expired"},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        email = request.data.get("email")
        otp_code = request.data.get("otp_code")

        if not email or not otp_code:
            return _error(
                data={"detail": "Email and OTP code are required"},
                message="Email and OTP code are required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            otp_record = (
                OtpRequest.objects.filter(email=email)
                .order_by("-created_at")
                .first()
            )

            if not otp_record:
                return _error(
                    data={"detail": "No OTP found for this email"},
                    message="No OTP found for this email",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if OTP is expired
            if timezone.now() > otp_record.expires_at:
                return _error(
                    data={"detail": "OTP has expired"},
                    message="OTP has expired",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if OTP is already used
            if otp_record.is_used:
                return _error(
                    data={"detail": "OTP has already been used"},
                    message="OTP has already been used",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if too many attempts
            if otp_record.attempt_count >= 3:
                return _error(
                    data={"detail": "Too many failed attempts"},
                    message="Too many failed attempts",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Verify OTP
            if otp_record.otp_code != otp_code:
                otp_record.attempt_count += 1
                otp_record.save()
                return _error(
                    data={"detail": "Invalid OTP code"},
                    message="Invalid OTP code",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Mark OTP as used
            otp_record.is_used = True
            otp_record.save()

            # Generate a short-lived token for password reset completion
            checkpoint_token = str(uuid.uuid4())
            expires_at = timezone.now() + timedelta(minutes=10)
            LoginCheckpoint.objects.create(
                user=otp_record.user,
                token=checkpoint_token,
                expires_at=expires_at,
                is_used=False,
            )

            # Log successful OTP verification
            log_audit_event(
                request=request,
                user=otp_record.user,
                action_type="password_otp_verified",
                model_name="User",
                object_id=str(otp_record.user.id),
                changes={"detail": f"Password reset OTP verified for {email}"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "message": "OTP verified successfully",
                    "email": email,
                    "verified": True,
                    "checkpoint_token": checkpoint_token,
                },
                message="OTP verified successfully",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.exception(f"Failed to verify OTP for {email}")
            return _error(
                data={"detail": "Failed to verify OTP"},
                message="Failed to verify OTP",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Password Reset Complete View
# ----------------------------------------------------------------------

class PasswordResetCompleteView(APIView):
    """
    Complete password reset with a new password.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Password Reset"],
        request=PasswordResetCompleteSerializer,
        responses={
            200: PasswordResetCompleteResponseSerializer,
            400: PasswordResetErrorResponseSerializer,
            500: PasswordResetErrorResponseSerializer,
        },
        description=(
            "Complete the password reset process by setting a new password. "
            "Requires a valid checkpoint token from the verification step."
        ),
        examples=[
            OpenApiExample(
                "Complete password reset request",
                value={
                    "checkpoint_token": "550e8400-e29b-41d4-a716-446655440000",
                    "new_password": "NewSecurePass123!",
                    "confirm_password": "NewSecurePass123!",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Complete password reset success",
                value={
                    "status": True,
                    "message": "Password reset successfully",
                    "data": {"message": "Password reset successfully"}
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Passwords do not match",
                value={"status": False, "detail": "Passwords do not match"},
                response_only=True,
                status_codes=["400"],
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
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        checkpoint_token = request.data.get("checkpoint_token")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not checkpoint_token or not new_password or not confirm_password:
            return _error(
                data={"detail": "Checkpoint token, new password and confirmation are required"},
                message="Checkpoint token, new password and confirmation are required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return _error(
                data={"detail": "Passwords do not match"},
                message="Passwords do not match",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Validate checkpoint
            checkpoint = LoginCheckpoint.objects.get(token=checkpoint_token)

            if not checkpoint.is_valid:
                return _error(
                    data={"detail": "Invalid or expired checkpoint token"},
                    message="Invalid or expired checkpoint token",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = checkpoint.user

            # Update password
            user.password = make_password(new_password)
            user.save()

            # Mark checkpoint as used
            checkpoint.is_used = True
            checkpoint.save()

            # Log successful password reset
            log_audit_event(
                request=request,
                user=user,
                action_type="PASSWORD_RESET",
                model_name="User",
                object_id=str(user.id),
                changes={"detail": "Password reset successfully"},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            # TODO: Send confirmation email
            logger.info(f"Password reset completed for user {user.email}")

            return _success(
                data={"message": "Password reset successfully"},
                message="Password reset successfully",
                status=status.HTTP_200_OK,
            )

        except LoginCheckpoint.DoesNotExist:
            return _error(
                data={"detail": "Invalid checkpoint token"},
                message="Invalid checkpoint token",
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Password reset failed: {exc}")

            log_audit_event(
                request=request,
                user=None,
                action_type="PASSWORD_RESET_FAILED",
                model_name="User",
                object_id="unknown",
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": "An error occurred during password reset"},
                message="An error occurred during password reset",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )