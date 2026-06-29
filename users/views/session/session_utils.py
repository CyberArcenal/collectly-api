# users/views/session_utils.py
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework import serializers
import logging

from audit.utils.log import log_audit_event
from users.models.blacklisted_token import BlacklistedAccessToken
from users.models.login_session import LoginSession
from users.models.security_log import SecurityLog
from users.utils.authentications import IsAuthenticatedAndNotBlacklisted
from users.serializers.LoginSession import LoginSessionReadSerializer
from utils.security import get_client_ip
from utils.response import _error

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class SessionRevokeErrorResponseSerializer(serializers.Serializer):
    """Error response for session revocation."""
    detail = serializers.CharField()


class SessionRevokeSuccessResponseSerializer(serializers.Serializer):
    """Success response (204 No Content - no body)."""
    pass


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class LoginSessionRevokeView(APIView):
    """
    Terminate/revoke a specific login session.
    This will:
    1. Blacklist the access token
    2. Blacklist the refresh token
    3. Mark the session as inactive
    4. Log the event
    """
    permission_classes = [IsAuthenticatedAndNotBlacklisted]

    @extend_schema(
        tags=["Session Management"],
        parameters=[
            OpenApiParameter(
                name="session_id",
                type=str,
                location=OpenApiParameter.PATH,
                description="UUID of the login session to revoke",
                required=True,
            ),
        ],
        responses={
            204: None,  # No content on success
            401: SessionRevokeErrorResponseSerializer,
            403: SessionRevokeErrorResponseSerializer,
            404: SessionRevokeErrorResponseSerializer,
            500: SessionRevokeErrorResponseSerializer,
        },
        description=(
            "Terminate a login session. This will blacklist both the access and refresh tokens, "
            "mark the session as inactive, and log the event. Users can only revoke their own sessions."
        ),
        examples=[
            OpenApiExample(
                "Success response",
                value=None,
                response_only=True,
                status_codes=["204"],
            ),
            OpenApiExample(
                "Session not found",
                value={"detail": "No LoginSession matches the given query."},
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Authentication failed",
                value={"detail": "Authentication credentials were not provided."},
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    @transaction.atomic
    def delete(self, request, session_id):
        user = request.user

        # Hanapin ang session record
        session = get_object_or_404(LoginSession, id=session_id, user=user, is_active=True)

        try:
            # BLACKLIST ACCESS TOKEN
            if session.access_token:
                BlacklistedAccessToken.blacklist_token(
                    jti=session.access_token,
                    user=user,
                    expires_at=timezone.now() + timezone.timedelta(days=1)
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
                details=f"Session terminated manually: {session.device_name} (Access token blacklisted)"
            )

            log_audit_event(
                request=request,
                user=user,
                action_type='logout',
                model_name='LoginSession',
                object_id=str(session.id),
                changes={'detail': 'Session revoked & both tokens blacklisted'},
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
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