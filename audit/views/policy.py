# audit/views/policy.py
import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.models.policy import AuditPolicy
from audit.serializers.AuditPolicy import (
    AuditPolicyReadSerializer,
    AuditPolicyListSerializer,
    AuditPolicyWriteSerializer,
)
from audit.services.policy import AuditPolicyService
from audit.utils.log import log_audit_event
from users.permissions.base import IsAccountActive, is_admin, is_staff
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

class AuditPolicyPaginationMetadataSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""
    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()


class AuditPolicyListResponseDataSerializer(serializers.Serializer):
    """Response data for GET /audit-policies/ (paginated list)"""
    pagination = AuditPolicyPaginationMetadataSerializer()
    data = AuditPolicyListSerializer(many=True)


class AuditPolicyListResponseSerializer(serializers.Serializer):
    """Full response for GET /audit-policies/ (paginated list)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditPolicyListResponseDataSerializer()


class AuditPolicyDetailResponseDataSerializer(serializers.Serializer):
    """Response data for GET /audit-policies/<id>/ (single)"""
    data = AuditPolicyReadSerializer()


class AuditPolicyDetailResponseSerializer(serializers.Serializer):
    """Full response for GET /audit-policies/<id>/ (single)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditPolicyReadSerializer()


class AuditPolicyCreateResponseDataSerializer(serializers.Serializer):
    """Response data for POST /audit-policies/ (201 Created)"""
    data = AuditPolicyReadSerializer()


class AuditPolicyCreateResponseSerializer(serializers.Serializer):
    """Full response for POST /audit-policies/ (201 Created)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditPolicyReadSerializer()


class AuditPolicyUpdateResponseDataSerializer(serializers.Serializer):
    """Response data for PUT/PATCH /audit-policies/<id>/ (200 OK)"""
    data = AuditPolicyReadSerializer()


class AuditPolicyUpdateResponseSerializer(serializers.Serializer):
    """Full response for PUT/PATCH /audit-policies/<id>/ (200 OK)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = AuditPolicyReadSerializer()


class AuditPolicyDeleteResponseDataSerializer(serializers.Serializer):
    """Response data for DELETE /audit-policies/<id>/ (204 No Content)"""
    pass


class AuditPolicyDeleteResponseSerializer(serializers.Serializer):
    """Full response for DELETE /audit-policies/<id>/ (204 No Content)"""
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(required=False, allow_null=True)


class AuditPolicyErrorResponseSerializer(serializers.Serializer):
    """Generic error response"""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()


class AuditPolicyValidationErrorSerializer(serializers.Serializer):
    """Validation error response (400)"""
    status = serializers.BooleanField(default=False)
    detail = serializers.CharField()
    data = serializers.DictField(required=False, allow_null=True)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class AuditPolicyCRUD(APIView):
    """
    CRUD operations for audit policies.
    - Admin/Staff users can create, update, and delete policies.
    - All authenticated users can view policies.
    - Policies may be immutable, preventing updates.
    """
    pagination_class = CustomPagination
    permission_classes = [
        IsAuthenticated,
        IsAccountActive,
    ]

    # ------------------------------------------------------------------
    # GET /audit-policies/  (list) and GET /audit-policies/<id>/ (retrieve)
    # No transaction needed for read operations
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Policies"],
        parameters=[
            OpenApiParameter(
                name="immutable",
                type=bool,
                description="Filter by immutable status",
                required=False,
            ),
            OpenApiParameter(
                name="retention_years",
                type=int,
                description="Filter by retention years",
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
            200: AuditPolicyListResponseSerializer,
            401: AuditPolicyErrorResponseSerializer,
            403: AuditPolicyErrorResponseSerializer,
            404: AuditPolicyErrorResponseSerializer,
            500: AuditPolicyErrorResponseSerializer,
        },
        description=(
            "Retrieve a single audit policy (if id provided) or a paginated list of all policies "
            "with optional filters."
        ),
        examples=[
            OpenApiExample(
                "List response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "pagination": {
                            "next": "http://example.com/api/v1/audit-policies/?page=2&page_size=10",
                            "previous": None,
                            "count": 25,
                            "current_page": 1,
                            "total_pages": 3,
                            "page_size": 10,
                        },
                        "data": [
                            {
                                "id": 1,
                                "retention_years": 5,
                                "immutable": True,
                                "created_at": "2025-01-01T00:00:00Z",
                                "policy_summary": "Retention: 5 years | Immutable: True",
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
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "read"

        try:
            if id is not None:
                policy = AuditPolicy.objects.filter(id=id).first()

                if not policy:
                    return _error(
                        data={"detail": "Audit policy not found."},
                        message="Audit policy not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = AuditPolicyReadSerializer(
                    policy, context={"request": request}
                )

                if user.is_authenticated:
                    log_audit_event(
                        request=request,
                        user=user,
                        action_type=action_type,
                        model_name="AuditPolicy",
                        object_id=str(policy.id),
                        changes={"detail": "Audit policy retrieved"},
                        ip_address=client_ip,
                        user_agent=user_agent,
                    )

                return _success(
                    data=serializer.data,
                    message="Audit policy retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List policies with filters
            qs = AuditPolicy.objects.all().order_by('-created_at')

            # Apply filters
            immutable = request.query_params.get("immutable")
            retention_years = request.query_params.get("retention_years")

            if immutable is not None:
                qs = qs.filter(immutable=immutable.lower() == "true")
            if retention_years:
                qs = qs.filter(retention_years=retention_years)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = AuditPolicyListSerializer(
                page, many=True, context={"request": request}
            )
            response = paginator.get_paginated_response(
                data=serializer.data,
                message="Audit policies retrieved successfully."
            )

            if user.is_authenticated:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type=action_type,
                    model_name="AuditPolicy",
                    object_id="multiple",
                    changes={
                        "detail": "Audit policy list retrieved",
                        "count": len(page) if page else 0,
                    },
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

            return response

        except Exception as exc:
            logger.exception("Audit policy retrieval error")
            if user.is_authenticated:
                log_audit_event(
                    request=request,
                    user=user,
                    action_type=action_type,
                    model_name="AuditPolicy",
                    object_id=str(id) if id else "multiple",
                    changes={"error": str(exc)},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )
            return _error(
                data={"detail": "An error occurred."},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /audit-policies/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Policies"],
        request=AuditPolicyWriteSerializer,
        responses={
            201: AuditPolicyCreateResponseSerializer,
            400: AuditPolicyValidationErrorSerializer,
            401: AuditPolicyErrorResponseSerializer,
            403: AuditPolicyErrorResponseSerializer,
            500: AuditPolicyErrorResponseSerializer,
        },
        description=(
            "Create a new audit policy. Admin/Staff only. "
            "Note: Only one policy is recommended, but multiple can exist."
        ),
        examples=[
            OpenApiExample(
                "Create policy request",
                value={
                    "retention_years": 5,
                    "immutable": True,
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
                        "retention_years": 5,
                        "immutable": True,
                        "immutable_display": "Yes",
                        "created_at": "2025-01-01T00:00:00Z",
                        "policy_summary": "Retention: 5 years | Immutable: True",
                    }
                },
                response_only=True,
                status_codes=["201"],
            ),
            OpenApiExample(
                "Validation error",
                value={
                    "status": False,
                    "detail": "Validation error.",
                    "data": {
                        "retention_years": ["Retention years must be positive."]
                    }
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @transaction.atomic
    def post(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "create"

        # Only admin/staff can create audit policies
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to create audit policies."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AuditPolicyWriteSerializer(
            data=request.data,
            context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
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
            policy = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(policy.id),
                changes={
                    "detail": "Audit policy created",
                    "retention_years": policy.retention_years,
                    "immutable": policy.immutable,
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = AuditPolicyReadSerializer(
                policy, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Audit policy created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit policy creation failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id="new",
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to create audit policy.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /audit-policies/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Policies"],
        request=AuditPolicyWriteSerializer,
        responses={
            200: AuditPolicyUpdateResponseSerializer,
            400: AuditPolicyValidationErrorSerializer,
            401: AuditPolicyErrorResponseSerializer,
            403: AuditPolicyErrorResponseSerializer,
            404: AuditPolicyErrorResponseSerializer,
            500: AuditPolicyErrorResponseSerializer,
        },
        description=(
            "Full update of an existing audit policy. Admin/Staff only. "
            "If the policy is immutable, updates are not allowed."
        ),
    )
    @transaction.atomic
    def put(self, request, id=None):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "update"

        # Only admin/staff can update audit policies
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to update audit policies."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        if not id:
            return _error(
                data={"detail": "Audit policy ID is required."},
                message="Audit policy ID is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            policy = AuditPolicy.objects.get(id=id)
            original_data = AuditPolicyReadSerializer(policy).data

        except AuditPolicy.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": "Audit policy not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "Audit policy not found."},
                message="Audit policy not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AuditPolicyWriteSerializer(
            policy,
            data=request.data,
            partial=False,
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
            updated_policy = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": AuditPolicyReadSerializer(updated_policy).data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = AuditPolicyReadSerializer(
                updated_policy, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Audit policy updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit policy update failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to update audit policy.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /audit-policies/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Policies"],
        request=AuditPolicyWriteSerializer,
        responses={
            200: AuditPolicyUpdateResponseSerializer,
            400: AuditPolicyValidationErrorSerializer,
            401: AuditPolicyErrorResponseSerializer,
            403: AuditPolicyErrorResponseSerializer,
            404: AuditPolicyErrorResponseSerializer,
            500: AuditPolicyErrorResponseSerializer,
        },
        description=(
            "Partial update of an existing audit policy. Admin/Staff only. "
            "If the policy is immutable, updates are not allowed."
        ),
        examples=[
            OpenApiExample(
                "Partial update request",
                value={"retention_years": 7},
                request_only=True,
            ),
            OpenApiExample(
                "Partial update response",
                value={
                    "status": True,
                    "message": "Success",
                    "data": {
                        "id": 1,
                        "retention_years": 7,
                        # ... other fields
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    @transaction.atomic
    def patch(self, request, id=None):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "partial_update"

        # Only admin/staff can update audit policies
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to update audit policies."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        if not id:
            return _error(
                data={"detail": "Audit policy ID is required."},
                message="Audit policy ID is required.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            policy = AuditPolicy.objects.get(id=id)
            original_data = AuditPolicyReadSerializer(policy).data

        except AuditPolicy.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": "Audit policy not found", "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "Audit policy not found."},
                message="Audit policy not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AuditPolicyWriteSerializer(
            policy,
            data=request.data,
            partial=True,
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
            updated_policy = serializer.save()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={
                    "before": original_data,
                    "after": AuditPolicyReadSerializer(updated_policy).data,
                    "modified_fields": list(request.data.keys()),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            read_serializer = AuditPolicyReadSerializer(
                updated_policy, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Audit policy updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit policy partial update failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": str(exc), "data": request.data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to update audit policy.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /audit-policies/<id>/
    # WITH TRANSACTION - proper rollback on errors
    # Admin/Staff only
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Audit Policies"],
        responses={
            204: AuditPolicyDeleteResponseSerializer,
            401: AuditPolicyErrorResponseSerializer,
            403: AuditPolicyErrorResponseSerializer,
            404: AuditPolicyErrorResponseSerializer,
            500: AuditPolicyErrorResponseSerializer,
        },
        description=(
            "Delete an audit policy. Admin/Staff only. "
            "If the policy is immutable, deletion is not allowed."
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
                value={"status": False, "detail": "Audit policy not found."},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    @transaction.atomic
    def delete(self, request, id):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        action_type = "delete"

        # Only admin/staff can delete audit policies
        if not is_admin(user) and not is_staff(user):
            return _error(
                data={"detail": "You do not have permission to delete audit policies."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            policy = AuditPolicy.objects.get(id=id)

            # Check if immutable
            if policy.immutable:
                return _error(
                    data={"detail": "Cannot delete an immutable policy."},
                    message="Cannot delete an immutable policy.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            policy_data = AuditPolicyReadSerializer(policy).data

            policy.delete()

            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"deleted_policy": policy_data},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _success(
                data=None,
                message="Audit policy deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except AuditPolicy.DoesNotExist:
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": "Audit policy not found"},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": "Audit policy not found."},
                message="Audit policy not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.error(f"Audit policy deletion failed: {exc}")
            log_audit_event(
                request=request,
                user=user,
                action_type=action_type,
                model_name="AuditPolicy",
                object_id=str(id),
                changes={"error": str(exc)},
                ip_address=client_ip,
                user_agent=user_agent,
            )
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete audit policy.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )