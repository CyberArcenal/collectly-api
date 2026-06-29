# users/views/jwt.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from django.utils import timezone
from django.db import transaction
from rest_framework.permissions import AllowAny
import logging
import uuid

from audit.utils.log import log_audit_event
from users.models import User
from users.models.login_session import LoginSession
from users.serializers.LoginSession import LoginSessionReadSerializer
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
# Request and Response serializers for documentation
# ----------------------------------------------------------------------

class RefreshTokenRequestSerializer(serializers.Serializer):
    """Request serializer for token refresh."""
    refresh = serializers.CharField(required=True, help_text="Refresh token")


class RefreshTokenResponseDataSerializer(serializers.Serializer):
    """Response data for token refresh."""
    refresh = serializers.CharField()
    access = serializers.CharField()
    message = serializers.CharField()


class RefreshTokenResponseSerializer(serializers.Serializer):
    """Full response for token refresh."""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = RefreshTokenResponseDataSerializer()


class RefreshTokenErrorResponseSerializer(serializers.Serializer):
    """Error response for token refresh."""
    detail = serializers.CharField()


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class RefreshTokenView(APIView):
    """
    Custom view for refreshing JWT tokens.
    Handles token refresh and updates LoginSession records.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=RefreshTokenRequestSerializer,
        responses={
            200: RefreshTokenResponseSerializer,
            400: RefreshTokenErrorResponseSerializer,
            401: RefreshTokenErrorResponseSerializer,
            404: RefreshTokenErrorResponseSerializer,
            500: RefreshTokenErrorResponseSerializer,
        },
        description=(
            "Refresh JWT tokens. This will invalidate the old refresh token "
            "and create a new pair of access and refresh tokens. "
            "It also updates the LoginSession record."
        ),
        examples=[
            OpenApiExample(
                "Refresh token request",
                value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
                request_only=True,
            ),
            OpenApiExample(
                "Refresh token response",
                value={
                    "status": True,
                    "message": "success",
                    "data": {
                        "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "message": "Tokens refreshed successfully"
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid token",
                value={"detail": "Invalid or expired refresh token"},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_name = user_agent[:100]  # Truncate to fit max_length=100

        refresh_token_str = request.data.get('refresh')

        if not refresh_token_str:
            return _error(
                data={"detail": "Refresh token is required"},
                message="Refresh token is required",
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Verify and refresh the token
            refresh_token = RefreshToken(refresh_token_str)
            user_id = refresh_token['user_id']

            try:
                user = User.objects.get(id=user_id)

                # Get the old JTI before refreshing
                old_refresh_jti = refresh_token['jti']

                # Refresh the token (this creates a new refresh token)
                new_refresh_token = RefreshToken.for_user(user)
                new_access_token = new_refresh_token.access_token

                # Get new JTIs
                new_refresh_jti = new_refresh_token['jti']
                new_access_jti = new_access_token['jti']

                # Calculate new expiration
                from django.conf import settings
                refresh_lifetime = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME', timezone.timedelta(days=7))
                new_expires_at = timezone.now() + refresh_lifetime

                # Update LoginSession or create new one
                try:
                    # Try to find existing session with the old refresh token
                    login_session = LoginSession.objects.get(refresh_token=old_refresh_jti)
                    login_session.refresh_token = new_refresh_jti
                    login_session.access_token = new_access_jti
                    login_session.expires_at = new_expires_at
                    login_session.last_used = timezone.now()
                    login_session.save()
                except LoginSession.DoesNotExist:
                    # Create new session if not found
                    LoginSession.objects.create(
                        id=uuid.uuid4(),
                        user=user,
                        device_name=device_name,
                        ip_address=client_ip,
                        expires_at=new_expires_at,
                        refresh_token=new_refresh_jti,
                        access_token=new_access_jti
                    )

                # Log successful token refresh
                log_audit_event(
                    request=request,
                    user=user,
                    action_type="TOKEN_REFRESH",
                    model_name="User",
                    object_id=str(user.id),
                    changes={"detail": "JWT tokens refreshed successfully"},
                    ip_address=client_ip,
                    user_agent=user_agent
                )

                return _success(
                    data={
                        "refresh": str(new_refresh_token),
                        "access": str(new_access_token),
                        "message": "Tokens refreshed successfully"
                    },
                    message="Tokens refreshed successfully",
                    status=status.HTTP_200_OK
                )

            except User.DoesNotExist:
                logger.error(f"User not found during token refresh: {user_id}")
                transaction.set_rollback(True)
                return _error(
                    data={"detail": "User not found"},
                    message="User not found",
                    status=status.HTTP_404_NOT_FOUND
                )

        except TokenError as e:
            transaction.set_rollback(True)
            logger.warning(f"Token refresh failed: {str(e)}")

            # Log failed token refresh attempt
            log_audit_event(
                request=request,
                user=None,
                action_type="TOKEN_REFRESH_FAILED",
                model_name="User",
                object_id="unknown",
                changes={"error": str(e), "client_ip": client_ip},
                ip_address=client_ip,
                user_agent=user_agent
            )

            return _error(
                data={"detail": "Invalid or expired refresh token"},
                message="Invalid or expired refresh token",
                status=status.HTTP_401_UNAUTHORIZED
            )

        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Unexpected error during token refresh: {str(e)}")

            # Log unexpected error
            log_audit_event(
                request=request,
                user=None,
                action_type="TOKEN_REFRESH_ERROR",
                model_name="User",
                object_id="unknown",
                changes={"error": str(e), "client_ip": client_ip},
                ip_address=client_ip,
                user_agent=user_agent
            )

            return _error(
                data={"detail": "An error occurred during token refresh"},
                message="An error occurred during token refresh",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )