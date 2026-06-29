# users/views/session.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import models
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
import logging

from audit.utils.log import log_audit_event
from users.models import User
from users.models.login_session import LoginSession
from users.permissions.base import IsAccountActive, is_admin
from users.serializers.LoginSession import (
    LoginSessionReadSerializer,
    LoginSessionWriteSerializer,
)
from utils.response import CustomPagination, _success, _error
from utils.security import get_client_ip
from rest_framework import serializers

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation (matching CustomPagination)
# ----------------------------------------------------------------------

class SessionPaginationMetadataSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""
    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class SessionDetailResponseSerializer(serializers.Serializer):
    """Response for GET /sessions/<id>/ (single session)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = LoginSessionReadSerializer()


class SessionListResponseSerializer(serializers.Serializer):
    """Response for GET /sessions/ (paginated list)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    pagination = SessionPaginationMetadataSerializer()
    data = LoginSessionReadSerializer(many=True)


class SessionCreateResponseSerializer(serializers.Serializer):
    """Response for POST /sessions/ (201 Created)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = LoginSessionReadSerializer()


class SessionUpdateResponseSerializer(serializers.Serializer):
    """Response for PUT/PATCH /sessions/<id>/ (200 OK)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = LoginSessionReadSerializer()


class SessionDeleteResponseSerializer(serializers.Serializer):
    """Response for DELETE /sessions/<id>/ (204 No Content)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField(default="Success")
    data = serializers.DictField(required=False, allow_null=True)


class SessionErrorResponseSerializer(serializers.Serializer):
    """Generic error response"""
    detail = serializers.CharField()


class SessionValidationErrorSerializer(serializers.Serializer):
    """Validation error response (400)"""
    detail = serializers.CharField()


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class LoginSessionCRUD(APIView):
    """
    CRUD operations for login sessions.
    - Admin users can view/manage all sessions
    - Regular users can only view/manage their own sessions
    """
    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        IsAccountActive,
    ]

    # ------------------------------------------------------------------
    # GET /sessions/  (list)  and  GET /sessions/<id>/  (retrieve)
    # No transaction needed for read operations
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Session Management"],
        parameters=[
            OpenApiParameter(
                name="is_active",
                type=bool,
                description="Filter by active status (true/false)",
                required=False,
            ),
            OpenApiParameter(
                name="is_valid",
                type=bool,
                description="Filter by validity (active and not expired)",
                required=False,
            ),
            OpenApiParameter(
                name="device_name",
                type=str,
                description="Filter by device name (case-insensitive, partial match)",
                required=False,
            ),
            OpenApiParameter(
                name="search",
                type=str,
                description="Search in device_name, ip_address, username, or email",
                required=False,
            ),
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
            200: SessionListResponseSerializer,
            401: SessionErrorResponseSerializer,
            403: SessionErrorResponseSerializer,
            404: SessionErrorResponseSerializer,
            500: SessionErrorResponseSerializer,
        },
        description=(
            "Retrieve a single login session (if id provided) or a paginated list of all login sessions "
            "with optional filters. Admin users can access all sessions; regular users can only access their own."
        ),
    )
    def get(self, request, id=None):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "read"

        try:
            if id is not None:
                try:
                    if is_admin(user):
                        login_session = LoginSession.objects.get(pk=id)
                    else:
                        login_session = LoginSession.objects.get(pk=id, user=user)
                except LoginSession.DoesNotExist:
                    return _error(
                        data=[],
                        message="Login session not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Check if user has permission to view this session
                if not is_admin(user) and login_session.user != user:
                    return _error(
                        data=[],
                        message="You do not have permission to view this login session.",
                        status=status.HTTP_403_FORBIDDEN,
                    )

                serializer = LoginSessionReadSerializer(
                    login_session, context={"request": request}
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type=action_type,
                    model_name="LoginSession",
                    object_id=str(login_session.id),
                    changes={"detail": "Login session retrieved"},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Login session retrieved successfully.",
                )

            # List login sessions with filters
            if is_admin(user):
                qs = LoginSession.objects.all().order_by("-last_used")
            else:
                qs = LoginSession.objects.filter(user=user).order_by("-last_used")

            # Apply filters
            is_active = request.query_params.get("is_active")
            is_valid = request.query_params.get("is_valid")
            device_name = request.query_params.get("device_name")
            search = request.query_params.get("search")

            if is_active:
                qs = qs.filter(is_active=is_active.lower() == "true")
            if is_valid:
                if is_valid.lower() == "true":
                    qs = qs.filter(is_active=True, expires_at__gt=timezone.now())
                else:
                    qs = qs.filter(
                        models.Q(is_active=False) | models.Q(expires_at__lte=timezone.now())
                    )
            if device_name:
                qs = qs.filter(device_name__icontains=device_name)
            if search:
                qs = qs.filter(
                    models.Q(device_name__icontains=search) |
                    models.Q(ip_address__icontains=search) |
                    models.Q(user__username__icontains=search) |
                    models.Q(user__email__icontains=search)
                )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = LoginSessionReadSerializer(
                page, many=True, context={"request": request}
            )
            response = paginator.get_paginated_response(
                data=serializer.data,
                message="Login sessions retrieved successfully.",
            )

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id="multiple",
                changes={
                    "detail": "Login session list retrieved",
                    "count": len(page) if page else 0,
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except LoginSession.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id) if id else "unknown",
                changes={"error": "Login session not found"},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=[],
                message="Login session not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as exc:
            logger.exception("Login session retrieval error")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id) if id else "multiple",
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="An error occurred while processing your request.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /sessions/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Session Management"],
        request=LoginSessionWriteSerializer,
        responses={
            201: SessionCreateResponseSerializer,
            400: SessionValidationErrorSerializer,
            401: SessionErrorResponseSerializer,
            403: SessionErrorResponseSerializer,
            500: SessionErrorResponseSerializer,
        },
        description=(
            "Create a new login session. Regular users can only create sessions for themselves; "
            "admin users can create sessions for any user."
        ),
        examples=[
            OpenApiExample(
                "Create session request",
                value={
                    "user": 1,
                    "device_name": "Chrome Browser on Windows",
                    "ip_address": "192.168.1.100",
                    "expires_at": "2025-12-31T23:59:59Z",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create session response",
                value={
                    "id": "a1b2c3d4-1234-5678-9abc-def012345678",
                    "user_data": {
                        "id": 1,
                        "full_name": "Admin User",
                        "username": "admin",
                        "email": "admin@example.com",
                        "first_name": "Admin",
                        "last_name": "User",
                        "user_type": "admin",
                        "avatar": None,
                    },
                    "device_name": "Chrome Browser on Windows",
                    "ip_address": "192.168.1.100",
                    "created_at": "2025-01-02T00:00:00Z",
                    "last_used": "2025-01-02T00:00:00Z",
                    "expires_at": "2025-12-31T23:59:59Z",
                    "is_active": True,
                    "status_display": "Active",
                    "is_valid_display": True,
                },
                response_only=True,
                status_codes=["201"],
            ),
            OpenApiExample(
                "Validation error",
                value={"detail": "Expiration date must be in the future."},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "create"

        logger.info(f"Login session creation: {request.data}")

        # If user is not staff, ensure they can only create sessions for themselves
        if not is_admin(user) and 'user' in request.data and str(request.data['user']) != str(user.id):
            return _error(
                data=[],
                message="You can only create login sessions for yourself.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LoginSessionWriteSerializer(
            data=request.data,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            login_session = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(login_session.id),
                changes=serializer.data,
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_data = LoginSessionReadSerializer(
                login_session, context={"request": request}
            ).data

            return _success(
                data=read_data,
                message="Login session created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Login session creation failed: {exc}")

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id="new",
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": str(exc)},
                message="Failed to create login session.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /sessions/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Session Management"],
        request=LoginSessionWriteSerializer,
        responses={
            200: SessionUpdateResponseSerializer,
            400: SessionValidationErrorSerializer,
            401: SessionErrorResponseSerializer,
            403: SessionErrorResponseSerializer,
            404: SessionErrorResponseSerializer,
            500: SessionErrorResponseSerializer,
        },
        description=(
            "Full update of an existing login session. Admin users can update any session; "
            "regular users can only update their own sessions."
        ),
    )
    @transaction.atomic
    def put(self, request, id):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "update"

        try:
            login_session = LoginSession.objects.get(pk=id)

            if not is_admin(user) and login_session.user != user:
                return _error(
                    data=[],
                    message="You do not have permission to update this login session.",
                    status=status.HTTP_403_FORBIDDEN,
                )

            original_data = LoginSessionReadSerializer(login_session).data

        except LoginSession.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": "Login session not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=[],
                message="Login session not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(f"Updating login session {id}: {request.data}")

        serializer = LoginSessionWriteSerializer(
            login_session,
            data=request.data,
            partial=False,
            context={"request": request},
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated_login_session = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": serializer.data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_data = LoginSessionReadSerializer(
                updated_login_session, context={"request": request}
            ).data

            return _success(
                data=read_data,
                message="Login session updated successfully.",
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Login session update failed: {exc}")

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": str(exc)},
                message="Failed to update login session.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /sessions/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Session Management"],
        request=LoginSessionWriteSerializer,
        responses={
            200: SessionUpdateResponseSerializer,
            400: SessionValidationErrorSerializer,
            401: SessionErrorResponseSerializer,
            403: SessionErrorResponseSerializer,
            404: SessionErrorResponseSerializer,
            500: SessionErrorResponseSerializer,
        },
        description=(
            "Partial update of an existing login session. Admin users can update any session; "
            "regular users can only update their own sessions."
        ),
        examples=[
            OpenApiExample(
                "Partial update request",
                value={
                    "is_active": False,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Partial update response",
                value={
                    "id": "a1b2c3d4-1234-5678-9abc-def012345678",
                    "user_data": {"username": "name"},
                    "device_name": "Chrome Browser on Windows",
                    "ip_address": "192.168.1.100",
                    "created_at": "2025-01-02T00:00:00Z",
                    "last_used": "2025-01-02T00:00:00Z",
                    "expires_at": "2025-12-31T23:59:59Z",
                    "is_active": False,
                    "status_display": "Inactive",
                    "is_valid_display": False,
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    @transaction.atomic
    def patch(self, request, id):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "partial_update"

        try:
            login_session = LoginSession.objects.get(pk=id)

            if not is_admin(user) and login_session.user != user:
                return _error(
                    data=[],
                    message="You do not have permission to update this login session.",
                    status=status.HTTP_403_FORBIDDEN,
                )

            original_data = LoginSessionReadSerializer(login_session).data

        except LoginSession.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": "Login session not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=[],
                message="Login session not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(f"Partial update for login session {id}: {request.data}")

        serializer = LoginSessionWriteSerializer(
            login_session,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated_login_session = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": serializer.data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_data = LoginSessionReadSerializer(
                updated_login_session, context={"request": request}
            ).data

            return _success(
                data=read_data,
                message="Login session updated successfully.",
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Login session partial update failed: {exc}")

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": str(exc)},
                message="Failed to update login session.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /sessions/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Session Management"],
        responses={
            204: SessionDeleteResponseSerializer,
            401: SessionErrorResponseSerializer,
            403: SessionErrorResponseSerializer,
            404: SessionErrorResponseSerializer,
            500: SessionErrorResponseSerializer,
        },
        description=(
            "Delete a login session. Admin users can delete any session; "
            "regular users can only delete their own sessions."
        ),
        examples=[
            OpenApiExample(
                "Success response",
                value={"status": True, "message": "Success", "data": None},
                response_only=True,
                status_codes=["204"],
            ),
            OpenApiExample(
                "Not found",
                value={"detail": "Login session not found."},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    @transaction.atomic
    def delete(self, request, id):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "delete"

        try:
            login_session = LoginSession.objects.get(pk=id)

            if not is_admin(user) and login_session.user != user:
                return _error(
                    data=[],
                    message="You do not have permission to delete this login session.",
                    status=status.HTTP_403_FORBIDDEN,
                )

            login_session_data = LoginSessionReadSerializer(login_session).data

        except LoginSession.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": "Login session not found"},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=[],
                message="Login session not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            login_session.delete()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"deleted_login_session": login_session_data},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return Response(
                {
                    "status": True,
                    "message": "Login session deleted successfully.",
                    "data": None,
                },
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Login session deletion failed: {exc}")

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="LoginSession",
                object_id=str(id),
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _error(
                data={"detail": str(exc)},
                message="Failed to delete login session.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )