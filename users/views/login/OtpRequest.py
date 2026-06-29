# users/views/otp_request_crud.py
import logging
from django.db import transaction
from django.db import models
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from users.models import OtpRequest, User
from users.permissions.base import IsAccountActive, is_admin
from users.serializers.OtpRequest import (
    OtpRequestReadSerializer,
    OtpRequestWriteSerializer,
)
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
# Response serializers for documentation (matching CustomPagination)
# ----------------------------------------------------------------------

class OtpRequestPaginationMetadataSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""
    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class OtpRequestListResponseDataSerializer(serializers.Serializer):
    """Response data for GET /otp-requests/ (paginated list)"""
    pagination = OtpRequestPaginationMetadataSerializer()
    data = OtpRequestReadSerializer(many=True)


class OtpRequestListResponseSerializer(serializers.Serializer):
    """Full response for GET /otp-requests/ (paginated list)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = OtpRequestListResponseDataSerializer()


class OtpRequestDetailResponseDataSerializer(serializers.Serializer):
    """Response data for GET /otp-requests/<id>/ (single)"""
    data = OtpRequestReadSerializer()


class OtpRequestDetailResponseSerializer(serializers.Serializer):
    """Full response for GET /otp-requests/<id>/ (single)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = OtpRequestReadSerializer()


class OtpRequestCreateResponseDataSerializer(serializers.Serializer):
    """Response data for POST /otp-requests/ (201 Created)"""
    data = OtpRequestReadSerializer()


class OtpRequestCreateResponseSerializer(serializers.Serializer):
    """Full response for POST /otp-requests/ (201 Created)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = OtpRequestReadSerializer()


class OtpRequestUpdateResponseDataSerializer(serializers.Serializer):
    """Response data for PUT/PATCH /otp-requests/<id>/ (200 OK)"""
    data = OtpRequestReadSerializer()


class OtpRequestUpdateResponseSerializer(serializers.Serializer):
    """Full response for PUT/PATCH /otp-requests/<id>/ (200 OK)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = OtpRequestReadSerializer()


class OtpRequestDeleteResponseDataSerializer(serializers.Serializer):
    """Response data for DELETE /otp-requests/<id>/ (204 No Content)"""
    pass


class OtpRequestDeleteResponseSerializer(serializers.Serializer):
    """Full response for DELETE /otp-requests/<id>/ (204 No Content)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(required=False, allow_null=True)


class OtpRequestErrorResponseSerializer(serializers.Serializer):
    """Generic error response"""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


class OtpRequestValidationErrorSerializer(serializers.Serializer):
    """Validation error response (400)"""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()
    data = serializers.DictField(required=False, allow_null=True)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class OtpRequestCRUD(APIView):
    """
    CRUD operations for OTP requests.
    - Admin users can view/manage all OTP requests
    - Regular users can only view/manage their own OTP requests
    """
    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticatedAndNotBlacklisted,
        IsAccountActive,
    ]

    # ------------------------------------------------------------------
    # GET /otp-requests/  (list)  and  GET /otp-requests/<id>/  (retrieve)
    # No transaction needed for read operations
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["OTP Management"],
        parameters=[
            OpenApiParameter(
                name="email",
                type=str,
                description="Filter by email (case-insensitive, partial match)",
                required=False,
            ),
            OpenApiParameter(
                name="is_used",
                type=bool,
                description="Filter by used status (true/false)",
                required=False,
            ),
            OpenApiParameter(
                name="search",
                type=str,
                description="Search in email or otp_code",
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
            200: OtpRequestListResponseSerializer,
            401: OtpRequestErrorResponseSerializer,
            403: OtpRequestErrorResponseSerializer,
            404: OtpRequestErrorResponseSerializer,
            500: OtpRequestErrorResponseSerializer,
        },
        description=(
            "Retrieve a single OTP request (if id provided) or a paginated list of all OTP requests "
            "with optional filters. Admin users can access all requests; regular users can only access their own."
        ),
        examples=[
            OpenApiExample(
                "List response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "pagination": {
                            "next": "http://example.com/api/v1/otp-requests/?page=2&page_size=10",
                            "previous": None,
                            "count": 25,
                            "current_page": 1,
                            "total_pages": 3,
                            "page_size": 10
                        },
                        "data": [
                            {
                                "id": 1,
                                "user_data": {
                                    "id": 1,
                                    "full_name": "John Doe",
                                    "username": "johndoe",
                                    "email": "john@example.com",
                                    "first_name": "John",
                                    "last_name": "Doe",
                                    "user_type": "staff",
                                    "avatar": None,
                                },
                                "otp_code": "123456",
                                "email": "john@example.com",
                                "created_at": "2025-01-01T00:00:00Z",
                                "expires_at": "2025-01-01T00:10:00Z",
                                "is_used": False,
                                "attempt_count": 0,
                                "status_display": "Active",
                            }
                        ]
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
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
                        otp_request = OtpRequest.objects.get(pk=id)
                    else:
                        otp_request = OtpRequest.objects.get(pk=id, user=user)
                except OtpRequest.DoesNotExist:
                    return _error(
                        data={"detail": "OTP request not found."},
                        message="OTP request not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Check if user has permission to view this OTP request
                if not is_admin(user) and otp_request.user != user:
                    return _error(
                        data={"detail": "You do not have permission to view this OTP request."},
                        message="Permission denied",
                        status=status.HTTP_403_FORBIDDEN,
                    )

                serializer = OtpRequestReadSerializer(
                    otp_request, context={"request": request}
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type=action_type,
                    model_name="OtpRequest",
                    object_id=str(otp_request.id),
                    changes={"detail": "OTP request retrieved"},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="OTP request retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List OTP requests with filters
            if is_admin(user):
                qs = OtpRequest.objects.all().order_by("-created_at")
            else:
                qs = OtpRequest.objects.filter(user=user).order_by("-created_at")

            # Apply filters
            email = request.query_params.get("email")
            is_used = request.query_params.get("is_used")
            search = request.query_params.get("search")

            if email:
                qs = qs.filter(email__icontains=email)
            if is_used:
                qs = qs.filter(is_used=is_used.lower() == "true")
            if search:
                qs = qs.filter(
                    models.Q(email__icontains=search) |
                    models.Q(otp_code__icontains=search)
                )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = OtpRequestReadSerializer(
                page, many=True, context={"request": request}
            )
            response = paginator.get_paginated_response(
                data=serializer.data,
                message="OTP requests retrieved successfully."
            )

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id="multiple",
                changes={
                    "detail": "OTP request list retrieved",
                    "count": len(page) if page else 0,
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except OtpRequest.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id) if id else "unknown",
                changes={"error": "OTP request not found"},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "OTP request not found."},
                message="OTP request not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as exc:
            logger.exception("OTP request retrieval error")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id) if id else "multiple",
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "An error occurred while processing your request."},
                message="An error occurred",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /otp-requests/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["OTP Management"],
        request=OtpRequestWriteSerializer,
        responses={
            201: OtpRequestCreateResponseSerializer,
            400: OtpRequestValidationErrorSerializer,
            401: OtpRequestErrorResponseSerializer,
            403: OtpRequestErrorResponseSerializer,
            500: OtpRequestErrorResponseSerializer,
        },
        description=(
            "Create a new OTP request. Regular users can only create requests for themselves; "
            "admin users can create requests for any user."
        ),
        examples=[
            OpenApiExample(
                "Create OTP request",
                value={
                    "user": 1,
                    "email": "john@example.com",
                    "type": "email",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "id": 1,
                        "user_data": {
                            "id": 1,
                            "full_name": "John Doe",
                            "username": "johndoe",
                            "email": "john@example.com",
                            "first_name": "John",
                            "last_name": "Doe",
                            "user_type": "staff",
                            "avatar": None,
                        },
                        "otp_code": "123456",
                        "email": "john@example.com",
                        "created_at": "2025-01-01T00:00:00Z",
                        "expires_at": "2025-01-01T00:10:00Z",
                        "is_used": False,
                        "attempt_count": 0,
                        "status_display": "Active",
                    }
                },
                response_only=True,
                status_codes=["201"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "create"

        logger.info(f"OTP request creation: {request.data}")

        # If user is not admin, ensure they can only create OTP requests for themselves
        if not is_admin(user) and "user" in request.data and int(request.data["user"]) != user.id:
            return _error(
                data={"detail": "You can only create OTP requests for yourself."},
                message="Permission denied",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OtpRequestWriteSerializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id="new",
                changes={"error": serializer.errors, "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            otp_request = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(otp_request.id),
                changes=serializer.data,
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = OtpRequestReadSerializer(
                otp_request, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="OTP request created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"OTP request creation failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id="new",
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to create OTP request.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /otp-requests/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["OTP Management"],
        request=OtpRequestWriteSerializer,
        responses={
            200: OtpRequestUpdateResponseSerializer,
            400: OtpRequestValidationErrorSerializer,
            401: OtpRequestErrorResponseSerializer,
            403: OtpRequestErrorResponseSerializer,
            404: OtpRequestErrorResponseSerializer,
            500: OtpRequestErrorResponseSerializer,
        },
        description=(
            "Full update of an existing OTP request. Admin users can update any request; "
            "regular users can only update their own requests."
        ),
    )
    @transaction.atomic
    def put(self, request, id):
        user: User = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "update"

        try:
            otp_request = OtpRequest.objects.get(pk=id)

            if not is_admin(user) and otp_request.user != user:
                return _error(
                    data={"detail": "You do not have permission to update this OTP request."},
                    message="Permission denied",
                    status=status.HTTP_403_FORBIDDEN,
                )

            original_data = OtpRequestReadSerializer(otp_request).data

        except OtpRequest.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": "OTP request not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "OTP request not found."},
                message="OTP request not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(f"Updating OTP request {id}: {request.data}")

        serializer = OtpRequestWriteSerializer(
            otp_request,
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
            updated_otp_request = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": serializer.data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = OtpRequestReadSerializer(
                updated_otp_request, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="OTP request updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"OTP request update failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to update OTP request.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /otp-requests/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["OTP Management"],
        request=OtpRequestWriteSerializer,
        responses={
            200: OtpRequestUpdateResponseSerializer,
            400: OtpRequestValidationErrorSerializer,
            401: OtpRequestErrorResponseSerializer,
            403: OtpRequestErrorResponseSerializer,
            404: OtpRequestErrorResponseSerializer,
            500: OtpRequestErrorResponseSerializer,
        },
        description=(
            "Partial update of an existing OTP request. Admin users can update any request; "
            "regular users can only update their own requests."
        ),
        examples=[
            OpenApiExample(
                "Partial update request",
                value={"is_used": True},
                request_only=True,
            ),
            OpenApiExample(
                "Partial update response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "id": 1,
                        "user_data": {"username": "name"},
                        "otp_code": "123456",
                        "email": "john@example.com",
                        "created_at": "2025-01-01T00:00:00Z",
                        "expires_at": "2025-01-01T00:10:00Z",
                        "is_used": True,
                        "attempt_count": 0,
                        "status_display": "Used",
                    }
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
            otp_request = OtpRequest.objects.get(pk=id)

            if not is_admin(user) and otp_request.user != user:
                return _error(
                    data={"detail": "You do not have permission to update this OTP request."},
                    message="Permission denied",
                    status=status.HTTP_403_FORBIDDEN,
                )

            original_data = OtpRequestReadSerializer(otp_request).data

        except OtpRequest.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": "OTP request not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "OTP request not found."},
                message="OTP request not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(f"Partial update for OTP request {id}: {request.data}")

        serializer = OtpRequestWriteSerializer(
            otp_request,
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
            updated_otp_request = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": serializer.data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = OtpRequestReadSerializer(
                updated_otp_request, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="OTP request updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"OTP request partial update failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to update OTP request.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /otp-requests/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["OTP Management"],
        responses={
            204: OtpRequestDeleteResponseSerializer,
            401: OtpRequestErrorResponseSerializer,
            403: OtpRequestErrorResponseSerializer,
            404: OtpRequestErrorResponseSerializer,
            500: OtpRequestErrorResponseSerializer,
        },
        description=(
            "Delete an OTP request. Admin users can delete any request; "
            "regular users can only delete their own requests."
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
                value={"status": False, "detail": "OTP request not found."},
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

        if not is_admin(user):
            return _error(
                data={"detail": "Access denied. Admin only."},
                message="Access denied",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            otp_request = OtpRequest.objects.get(pk=id)

            if not is_admin(user) and otp_request.user != user:
                return _error(
                    data={"detail": "You do not have permission to delete this OTP request."},
                    message="Permission denied",
                    status=status.HTTP_403_FORBIDDEN,
                )

            otp_data = OtpRequestReadSerializer(otp_request).data

        except OtpRequest.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": "OTP request not found"},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "OTP request not found."},
                message="OTP request not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            otp_request.delete()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"deleted_otp_request": otp_data},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return Response(
                {
                    "status": True,
                    "message": "OTP request deleted successfully.",
                    "data": None,
                },
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"OTP request deletion failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="OtpRequest",
                object_id=str(id),
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete OTP request.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )