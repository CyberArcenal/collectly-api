# users/views/verify.py
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from django.utils import timezone
from django.contrib.auth import get_user_model

from users.models.blacklisted_token import BlacklistedAccessToken
from users.serializers.User import UserReadSerializer
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

class TokenVerifyRequestSerializer(serializers.Serializer):
    """Request serializer for token verification."""
    token = serializers.CharField(required=True, help_text="Access token to verify")


class TokenVerifySuccessDataSerializer(serializers.Serializer):
    """Response data for successful token verification."""
    valid = serializers.BooleanField()
    user = UserReadSerializer()


class TokenVerifySuccessResponseSerializer(serializers.Serializer):
    """Full response for successful token verification."""
    status = serializers.BooleanField()
    message = serializers.CharField()
    data = TokenVerifySuccessDataSerializer()


class TokenVerifyErrorResponseSerializer(serializers.Serializer):
    """Error response for token verification."""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


# ----------------------------------------------------------------------
# Token Verify View
# ----------------------------------------------------------------------

class TokenVerifyView(APIView):
    """
    Verify the validity of an access token.
    Checks:
    1. Token signature
    2. Token expiration
    3. Token type (must be access token)
    4. Token blacklist status
    5. User existence and active status
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=TokenVerifyRequestSerializer,
        responses={
            200: TokenVerifySuccessResponseSerializer,
            400: TokenVerifyErrorResponseSerializer,
            401: TokenVerifyErrorResponseSerializer,
        },
        description=(
            "Verify an access token's validity. Returns user data if the token is valid. "
            "Checks expiration, blacklist status, and user status."
        ),
        examples=[
            OpenApiExample(
                "Verify token request",
                value={"token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
                request_only=True,
            ),
            OpenApiExample(
                "Valid token response",
                value={
                    "status": True,
                    "message": "Token is valid",
                    "data": {
                        "valid": True,
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
                        }
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing token",
                value={"status": False, "detail": "Token is required"},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid token",
                value={"status": False, "detail": "Invalid or expired token"},
                response_only=True,
                status_codes=["401"],
            ),
            OpenApiExample(
                "Expired token",
                value={"status": False, "detail": "Token has expired"},
                response_only=True,
                status_codes=["401"],
            ),
            OpenApiExample(
                "Revoked token",
                value={"status": False, "detail": "Token has been revoked"},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        token_str = request.data.get("token")

        if not token_str:
            return _error(
                data={"detail": "Token is required"},
                message="Token is required",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Validate token - signature and expiry
            token = AccessToken(token_str)

            # Check token type
            if token.get("token_type") != "access":
                return _error(
                    data={"detail": "Only access tokens are allowed"},
                    message="Only access tokens are allowed",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check 1: Token expiration
            if token.get("exp") < timezone.now().timestamp():
                return _error(
                    data={"detail": "Token has expired"},
                    message="Token has expired",
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Check 2: Custom access token blacklist
            jti = token.get("jti")
            if BlacklistedAccessToken.is_blacklisted(jti):
                return _error(
                    data={"detail": "Token has been revoked"},
                    message="Token has been revoked",
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Check 3: User validation
            user_id = token.get("user_id")
            try:
                user = User.objects.get(id=user_id, is_active=True)
            except User.DoesNotExist:
                return _error(
                    data={"detail": "User not found or inactive"},
                    message="User not found or inactive",
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # Token is valid
            user_data = UserReadSerializer(user, context={"request": request}).data

            return _success(
                data={
                    "valid": True,
                    "user": user_data,
                },
                message="Token is valid",
                status=status.HTTP_200_OK,
            )

        except (InvalidToken, TokenError) as e:
            return _error(
                data={"detail": "Invalid or expired token"},
                message="Invalid or expired token",
                status=status.HTTP_401_UNAUTHORIZED,
            )

        except Exception as e:
            logger.exception(f"Token verification failed: {e}")
            return _error(
                data={"detail": "An error occurred during token verification"},
                message="An error occurred during token verification",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )