import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from loan_applications.models.loan_application import LoanApplication
from loan_applications.serializers.loan_application import (
    LoanApplicationReadSerializer,
    LoanApplicationListSerializer,
    LoanApplicationCreateSerializer,
    LoanApplicationUpdateSerializer,
    LoanApplicationApproveSerializer,
    LoanApplicationRejectSerializer,
)
from loan_applications.services.loan_application import LoanApplicationService
from users.permissions.base import IsAccountActive, can_read, can_edit, is_admin
from utils.helpers import filter_cleaner
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

class LoanApplicationStatsDataSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    approved = serializers.IntegerField()
    rejected = serializers.IntegerField()
    total_requested_amount = serializers.FloatField()
    average_requested_amount = serializers.FloatField()
    min_requested_amount = serializers.FloatField()
    max_requested_amount = serializers.FloatField()
    applications_last_30_days = serializers.IntegerField()


class LoanApplicationStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationStatsDataSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


class LoanApplicationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = LoanApplicationListSerializer(many=True)


class LoanApplicationDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationReadSerializer()


class LoanApplicationCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationReadSerializer()


class LoanApplicationUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationReadSerializer()


class LoanApplicationDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class LoanApplicationApproveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationReadSerializer()


class LoanApplicationRejectResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanApplicationReadSerializer()


class LoanApplicationStatisticsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------


class LoanApplicationCRUDView(APIView):
    """
    CRUD operations for loan applications.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /loan-applications/  (list) and GET /loan-applications/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Applications"],
        parameters=[
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
            OpenApiParameter(
                name="status",
                type=str,
                description="Filter by status (pending, approved, rejected)",
                required=False,
            ),
            OpenApiParameter(
                name="debtor_id",
                type=int,
                description="Filter by debtor ID",
                required=False,
            ),
            OpenApiParameter(
                name="search",
                type=str,
                description="Search by debtor name or purpose",
                required=False,
            ),
            OpenApiParameter(
                name="from_date",
                type=str,
                description="Filter from date",
                required=False,
            ),
            OpenApiParameter(
                name="to_date", type=str, description="Filter to date", required=False
            ),
            OpenApiParameter(
                name="include_deleted",
                type=bool,
                description="Include soft-deleted",
                required=False,
            ),
        ],
        responses={
            200: LoanApplicationListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single loan application (if id provided) or a paginated list.",
    )
    def get(self, request, id=None):
        """Retrieve single loan application or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={
                    "detail": "You do not have permission to view loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = (
                    request.query_params.get("include_deleted", "false").lower()
                    == "true"
                )
                application = LoanApplicationService.get_by_id(id, include_deleted)
                if not application:
                    return _error(
                        data={"detail": "Loan application not found."},
                        message="Loan application not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = LoanApplicationReadSerializer(
                    application, context={"request": request}
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="LoanApplication",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Loan application retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                "status": request.query_params.get("status"),
                "debtor_id": request.query_params.get("debtor_id"),
                "search": request.query_params.get("search"),
                "from_date": request.query_params.get("from_date"),
                "to_date": request.query_params.get("to_date"),
                "include_deleted": request.query_params.get(
                    "include_deleted", "false"
                ).lower()
                == "true",
            }
            filters = filter_cleaner(filters)

            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 20))
            sort_by = request.query_params.get("sort_by", "created_at")
            sort_order = request.query_params.get("sort_order", "desc")

            result = LoanApplicationService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )

            paginator = self.pagination_class()
            serialized_data = LoanApplicationListSerializer(
                result["data"], many=True, context={"request": request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Loan applications retrieved successfully.",
                pagination=result["pagination"],
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="LoanApplication",
                object_id="list",
                changes={"count": result["pagination"]["total"]},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Loan application retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /loan-applications/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Applications"],
        request=LoanApplicationCreateSerializer,
        responses={
            201: LoanApplicationCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new loan application. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        """Create a new loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        logger.debug(
            f"User {user.username} is attempting to create a loan application with data: {request.data}"
        )

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to create loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LoanApplicationCreateSerializer(data=request.data)

        if not serializer.is_valid(raise_exception=True):
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="LoanApplication",
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
            application = LoanApplicationService.create(
                data=serializer.validated_data, user=user, request=request
            )

            read_serializer = LoanApplicationReadSerializer(
                application, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Loan application created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /loan-applications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Applications"],
        request=LoanApplicationUpdateSerializer,
        responses={
            200: LoanApplicationUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing loan application. Admin/Staff only. Only pending applications can be updated.",
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to update loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        application = LoanApplicationService.get_by_id(id)
        if not application:
            return _error(
                data={"detail": "Loan application not found."},
                message="Loan application not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanApplicationUpdateSerializer(
            application, data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = LoanApplicationService.update(
                application_id=id,
                data=serializer.validated_data,
                user=user,
                request=request,
            )

            read_serializer = LoanApplicationReadSerializer(
                updated, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Loan application updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /loan-applications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Applications"],
        request=LoanApplicationUpdateSerializer,
        responses={
            200: LoanApplicationUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing loan application. Admin/Staff only. Only pending applications can be updated.",
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to update loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        application = LoanApplicationService.get_by_id(id)
        if not application:
            return _error(
                data={"detail": "Loan application not found."},
                message="Loan application not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanApplicationUpdateSerializer(
            application, data=request.data, partial=True, context={"request": request}
        )

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = LoanApplicationService.update(
                application_id=id,
                data=serializer.validated_data,
                user=user,
                request=request,
            )

            read_serializer = LoanApplicationReadSerializer(
                updated, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Loan application updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /loan-applications/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Applications"],
        responses={
            204: LoanApplicationDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a loan application. Admin/Staff only. Only pending applications can be deleted.",
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to delete loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        application = LoanApplicationService.get_by_id(id)
        if not application:
            return _error(
                data={"detail": "Loan application not found."},
                message="Loan application not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            LoanApplicationService.delete(application_id=id, user=user, request=request)

            return _success(
                data=None,
                message="Loan application deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Loan Application Approve View
# ----------------------------------------------------------------------


class LoanApplicationApproveView(APIView):
    """
    Approve a loan application.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        request=LoanApplicationApproveSerializer,
        responses={
            200: LoanApplicationApproveResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Approve a pending loan application. Admin/Manager only.",
    )
    @transaction.atomic
    def post(self, request, id):
        """Approve a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to approve loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        application = LoanApplicationService.get_by_id(id)
        if not application:
            return _error(
                data={"detail": "Loan application not found."},
                message="Loan application not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanApplicationApproveSerializer(
            data=request.data, context={"request": request, "user": user}
        )
        serializer.instance = application

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="update",
                model_name="LoanApplication",
                object_id=str(id),
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
            approved = serializer.save()

            read_serializer = LoanApplicationReadSerializer(
                approved, context={"request": request}
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="loan_application_approved",
                model_name="LoanApplication",
                object_id=str(id),
                changes={
                    "approved_by": serializer.validated_data.get(
                        "approved_by", user.username
                    )
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Loan application approved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application approval failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to approve loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Loan Application Reject View
# ----------------------------------------------------------------------


class LoanApplicationRejectView(APIView):
    """
    Reject a loan application.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        request=LoanApplicationRejectSerializer,
        responses={
            200: LoanApplicationRejectResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Reject a pending loan application. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request, id):
        """Reject a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={
                    "detail": "You do not have permission to reject loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        application = LoanApplicationService.get_by_id(id)
        if not application:
            return _error(
                data={"detail": "Loan application not found."},
                message="Loan application not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanApplicationRejectSerializer(
            data=request.data, context={"request": request}
        )
        serializer.instance = application

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="update",
                model_name="LoanApplication",
                object_id=str(id),
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
            rejected = serializer.save()

            read_serializer = LoanApplicationReadSerializer(
                rejected, context={"request": request}
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="loan_application_rejected",
                model_name="LoanApplication",
                object_id=str(id),
                changes={
                    "rejection_reason": serializer.validated_data.get(
                        "rejection_reason"
                    )
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Loan application rejected successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application rejection failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to reject loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Loan Application Statistics View
# ----------------------------------------------------------------------


class LoanApplicationStatisticsView(APIView):
    """
    Get loan application statistics.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        responses={
            200: LoanApplicationStatisticsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get loan application statistics including counts by status and total amounts.",
    )
    def get(self, request):
        """Get loan application statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={
                    "detail": "You do not have permission to view loan application statistics."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = LoanApplicationService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="LoanApplication",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Loan application statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Loan application statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve loan application statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# LOAN APPLICATION RESTORE VIEW
# ===================================================================


class LoanApplicationRestoreView(APIView):
    """
    Restore a soft-deleted loan application. Admin only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        responses={
            200: LoanApplicationDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted loan application. Admin only.",
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={
                    "detail": "You do not have permission to restore loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            application = LoanApplicationService.restore(
                application_id=id, user=user, request=request
            )

            serializer = LoanApplicationReadSerializer(
                application, context={"request": request}
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="LoanApplication",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Loan application restored successfully.",
                status=status.HTTP_200_OK,
            )

        except ValidationError as e:
            return _error(
                data=e.message_dict,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# LOAN APPLICATION PERMANENT DELETE VIEW
# ===================================================================


class LoanApplicationPermanentDeleteView(APIView):
    """
    Permanently delete a loan application (hard delete). Admin only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        responses={
            204: LoanApplicationDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a loan application (hard delete). Admin only. Only pending applications can be permanently deleted.",
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a loan application."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={
                    "detail": "You do not have permission to permanently delete loan applications."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            LoanApplicationService.permanent_delete(
                application_id=id, user=user, request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="LoanApplication",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Loan application permanently deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except ValidationError as e:
            return _error(
                data=e.message_dict,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan application permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete loan application.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            
            
            











# ============================================================
# View
# ============================================================

class LoanApplicationStatisticsView(APIView):
    """
    Get loan application statistics.

    Returns comprehensive statistics about all loan applications:
    - Total, pending, approved, rejected counts
    - Requested amount statistics (total, average, min, max)
    - Applications in the last 30 days
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Applications"],
        summary="Get loan application statistics",
        description="Returns comprehensive statistics about loan applications including counts by status and amount analysis.",
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=str,
                description="Filter applications from this date (YYYY-MM-DD)",
                required=False,
            ),
            OpenApiParameter(
                name="end_date",
                type=str,
                description="Filter applications up to this date (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: LoanApplicationStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                name="Success Response",
                value={
                    "status": True,
                    "message": "Statistics retrieved successfully.",
                    "data": {
                        "total": 125,
                        "pending": 12,
                        "approved": 87,
                        "rejected": 26,
                        "total_requested_amount": 1575000.00,
                        "average_requested_amount": 12600.00,
                        "min_requested_amount": 1000.00,
                        "max_requested_amount": 50000.00,
                        "applications_last_30_days": 18,
                    }
                },
                status_codes=["200"],
            ),
            OpenApiExample(
                name="Unauthorized",
                value={"status": False, "message": "Authentication credentials were not provided.", "data": None},
                status_codes=["401"],
            ),
            OpenApiExample(
                name="Forbidden",
                value={"status": False, "message": "You do not have permission to view application statistics.", "data": None},
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request):
        try:
            start_date = request.query_params.get('start_date')
            end_date = request.query_params.get('end_date')

            stats = LoanApplicationService.get_statistics(
                start_date=start_date,
                end_date=end_date
            )

            return _success(
                data=stats,
                message="Statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.exception("Loan application stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve loan application statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
