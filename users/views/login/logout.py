# users/views/logout.py
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.exceptions import TokenError

from audit.utils.log import log_audit_event
from users.models import User
from users.models.blacklisted_token import BlacklistedAccessToken
from users.models.login_session import LoginSession
from users.models.security_log import SecurityLog
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

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Request/Response serializers for documentation
# ----------------------------------------------------------------------

class LogoutRequestSerializer(serializers.Serializer):
    """Request serializer for logout."""
    refresh = serializers.CharField(required=True, help_text="Refresh token")


class LogoutSuccessResponseDataSerializer(serializers.Serializer):
    """Response data for successful logout."""
    message = serializers.CharField()


class LogoutSuccessResponseSerializer(serializers.Serializer):
    """Full response for successful logout."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = LogoutSuccessResponseDataSerializer(required=False, allow_null=True)


class LogoutErrorResponseSerializer(serializers.Serializer):
    """Error response for logout."""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


class LogoutAllSuccessResponseDataSerializer(serializers.Serializer):
    """Response data for successful logout all."""
    message = serializers.CharField()


class LogoutAllSuccessResponseSerializer(serializers.Serializer):
    """Full response for successful logout all."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = LogoutAllSuccessResponseDataSerializer(required=False, allow_null=True)


# ----------------------------------------------------------------------
# Logout View
# ----------------------------------------------------------------------

class LogoutView(APIView):
    """
    Logout from a specific session by blacklisting the refresh token
    and its associated access token.
    """
    permission_classes = []  # Allow any user with valid token

    @extend_schema(
        tags=["Authentication"],
        request=LogoutRequestSerializer,
        responses={
            200: LogoutSuccessResponseSerializer,
            400: LogoutErrorResponseSerializer,
            401: LogoutErrorResponseSerializer,
            404: LogoutErrorResponseSerializer,
            500: LogoutErrorResponseSerializer,
        },
        description=(
            "Logout from the current session. This will blacklist both the refresh "
            "and access tokens, and mark the login session as inactive."
        ),
        examples=[
            OpenApiExample(
                "Logout request",
                value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
                request_only=True,
            ),
            OpenApiExample(
                "Logout success",
                value={
                    "status": True,
                    "message": "Logged out successfully",
                    "data": {"message": "Logged out successfully"}
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing refresh token",
                value={"status": False, "detail": "Refresh token is required"},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid token",
                value={"status": False, "detail": "Invalid refresh token"},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        user = request.user

        refresh_token_str = request.data.get("refresh")

        if not refresh_token_str:
            return _error(
                data={"detail": "Refresh token is required"},
                message="Refresh token is required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh_token = RefreshToken(refresh_token_str)
            user_id = refresh_token["user_id"]
            refresh_jti = refresh_token["jti"]
            access_jti = refresh_token.access_token["jti"]

            try:
                user = User.objects.get(id=user_id)

                # Find the login session
                session = LoginSession.objects.filter(
                    user=user, refresh_token=refresh_jti, is_active=True
                ).first()

                # Blacklist access token
                if access_jti:
                    BlacklistedAccessToken.blacklist_token(
                        jti=access_jti,
                        user=user,
                        expires_at=timezone.now() + timezone.timedelta(days=1),
                    )
                    logger.info(f"Blacklisted access token JTI: {access_jti}")

                # Blacklist the refresh token
                try:
                    refresh_token.blacklist()
                except Exception:
                    # Fallback: manually blacklist refresh token
                    try:
                        outstanding = OutstandingToken.objects.get(jti=refresh_jti)
                        BlacklistedToken.objects.get_or_create(token=outstanding)
                    except OutstandingToken.DoesNotExist:
                        pass

                # Mark session as inactive if exists
                if session:
                    session.is_active = False
                    session.expires_at = timezone.now()
                    session.save(update_fields=["is_active", "expires_at"])

                # Cleanup expired blacklisted tokens
                BlacklistedAccessToken.cleanup_expired()

                # Log security event
                SecurityLog.objects.create(
                    user=user,
                    event_type="logout",
                    ip_address=client_ip,
                    user_agent=user_agent,
                    details="User logged out (tokens blacklisted)",
                )

                # Log audit event
                log_audit_event(
                    request=request,
                    user=user,
                    action_type="logout",
                    model_name="User",
                    object_id=str(user.id),
                    changes={"detail": "User logged out successfully (tokens blacklisted)"},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data={"message": "Logged out successfully"},
                    message="Logged out successfully",
                    status=status.HTTP_200_OK,
                )

            except User.DoesNotExist:
                logger.warning(f"User not found during logout: {user_id}")
                return _error(
                    data={"detail": "User not found"},
                    message="User not found",
                    status=status.HTTP_404_NOT_FOUND,
                )

        except TokenError as e:
            transaction.set_rollback(True)
            logger.warning(f"Logout failed due to invalid token: {str(e)}")
            return _error(
                data={"detail": "Invalid refresh token"},
                message="Invalid refresh token",
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Unexpected error during logout: {str(e)}")
            return _error(
                data={"detail": "An error occurred during logout"},
                message="An error occurred during logout",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Logout All View
# ----------------------------------------------------------------------

class LogoutAllView(APIView):
    """
    Logout from all active sessions by blacklisting all tokens
    and marking all sessions as inactive.
    """
    permission_classes = []  # Allow any user with valid token

    @extend_schema(
        tags=["Authentication"],
        responses={
            200: LogoutAllSuccessResponseSerializer,
            401: LogoutErrorResponseSerializer,
            500: LogoutErrorResponseSerializer,
        },
        description=(
            "Logout from all active devices/sessions. This will blacklist all "
            "access and refresh tokens, and mark all login sessions as inactive."
        ),
        examples=[
            OpenApiExample(
                "Logout all success",
                value={
                    "status": True,
                    "message": "Logged out from 3 devices successfully",
                    "data": {"message": "Logged out from 3 devices successfully"}
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Authentication required",
                value={"status": False, "detail": "Authentication required"},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        user = request.user

        if not user.is_authenticated:
            return _error(
                data={"detail": "Authentication required"},
                message="Authentication required",
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            # Get all active sessions for the user
            active_sessions = LoginSession.objects.filter(user=user, is_active=True)
            terminated_count = 0

            # Blacklist all tokens and mark sessions inactive
            for session in active_sessions:
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
                        token = RefreshToken()
                        token["jti"] = session.refresh_token
                        token.blacklist()
                    except Exception:
                        try:
                            outstanding = OutstandingToken.objects.get(
                                jti=session.refresh_token
                            )
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

            # Log security event
            SecurityLog.objects.create(
                user=user,
                event_type="logout",
                ip_address=client_ip,
                user_agent=user_agent,
                details=f"All sessions terminated ({terminated_count} sessions, tokens blacklisted)",
            )

            # Log audit event
            log_audit_event(
                request=request,
                user=user,
                action_type="logout_all",
                model_name="User",
                object_id=str(user.id),
                changes={
                    "detail": f"User logged out from all devices ({terminated_count} sessions terminated)"
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"message": f"Logged out from {terminated_count} devices successfully"},
                message=f"Logged out from {terminated_count} devices successfully",
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Unexpected error during logout all: {str(e)}")
            return _error(
                data={"detail": "An error occurred during logout"},
                message="An error occurred during logout",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )