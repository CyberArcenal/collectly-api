import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from debts.serializers.forgiveness_log import (
    ForgivenessLogReadSerializer,
    ForgivenessLogListSerializer,
    ForgivenessLogCreateSerializer,
)
from debts.services.forgiveness import ForgivenessService
from users.permissions.base import IsAccountActive, can_read, can_edit, is_admin
from utils.response import BasePaginatedSerializer, CustomPagination, _success, _error
from utils.security import get_client_ip

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class ForgivenessLogListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = ForgivenessLogListSerializer(many=True)


class ForgivenessLogDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = ForgivenessLogReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class ForgivenessLogCRUDView(APIView):
    """
    CRUD operations for forgiveness logs.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /forgiveness-logs/  (list) and GET /forgiveness-logs/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Forgiveness"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="debt_id", type=int, description="Filter by debt ID", required=False),
            OpenApiParameter(name="borrower_id", type=int, description="Filter by borrower ID", required=False),
            OpenApiParameter(name="status", type=str, description="Filter by status", required=False),
        ],
        responses={
            200: ForgivenessLogListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single forgiveness log (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single forgiveness log or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view forgiveness logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                log_entry = ForgivenessService.get_by_id(id)
                if not log_entry:
                    return _error(
                        data={"detail": "Forgiveness log not found."},
                        message="Forgiveness log not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = ForgivenessLogReadSerializer(log_entry, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="ForgivenessLog",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Forgiveness log retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            debt_id = request.query_params.get('debt_id')
            if debt_id:
                page = int(request.query_params.get('page', 1))
                limit = int(request.query_params.get('page_size', 20))

                result = ForgivenessService.get_by_debt(
                    debt_id=debt_id,
                    page=page,
                    limit=limit
                )

                paginator = self.pagination_class()
                response = paginator.get_paginated_response(
                    data=result['data'],
                    message="Forgiveness logs retrieved successfully.",
                    pagination=result['pagination']
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="ForgivenessLog",
                    object_id="list",
                    changes={"debt_id": debt_id},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return response

            return _error(
                data={"detail": "debt_id parameter is required for listing."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as exc:
            logger.exception("Forgiveness log retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /forgiveness-logs/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Forgiveness"],
        request=ForgivenessLogCreateSerializer,
        responses={
            201: ForgivenessLogDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Apply forgiveness to a debt. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Apply forgiveness to a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to apply forgiveness."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ForgivenessLogCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="ForgivenessLog",
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
            data = serializer.validated_data
            debt_id = data['debt'].id
            amount_forgiven = data['amount_forgiven']
            reason = data.get('reason')

            log_entry = ForgivenessService.apply_forgiveness(
                debt_id=debt_id,
                amount_forgiven=amount_forgiven,
                reason=reason,
                user=user,
                request=request
            )

            read_serializer = ForgivenessLogReadSerializer(log_entry, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Forgiveness applied successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Forgiveness application failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to apply forgiveness.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /forgiveness-logs/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Forgiveness"],
        responses={
            204: inline_serializer(
                name="DeleteSuccessResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a forgiveness log. Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a forgiveness log."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to delete forgiveness logs."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        log_entry = ForgivenessService.get_by_id(id)
        if not log_entry:
            return _error(
                data={"detail": "Forgiveness log not found."},
                message="Forgiveness log not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            ForgivenessService.delete(
                log_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Forgiveness log deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Forgiveness log deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete forgiveness log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )