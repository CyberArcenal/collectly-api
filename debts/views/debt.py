import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from decimal import Decimal

from audit.utils.log import log_audit_event
from debts.models.debt import Debt
from debts.serializers.debt import (
    DebtReadSerializer,
    DebtListSerializer,
    DebtCreateSerializer,
    DebtUpdateSerializer,
)
from debts.services.debt import DebtService
from debts.services.interest_accrual import InterestAccrualService
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

class DebtListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = DebtListSerializer(many=True)


class DebtDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtReadSerializer()


class DebtCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtReadSerializer()


class DebtUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DebtReadSerializer()


class DebtDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class DebtCRUDView(APIView):
    """
    CRUD operations for debts.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /debts/  (list) and GET /debts/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Debts"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="search", type=str, description="Search by name or borrower", required=False),
            OpenApiParameter(name="status", type=str, description="Filter by status", required=False),
            OpenApiParameter(name="borrower_id", type=int, description="Filter by borrower ID", required=False),
            OpenApiParameter(name="due_date_from", type=str, description="Due date from (YYYY-MM-DD)", required=False),
            OpenApiParameter(name="due_date_to", type=str, description="Due date to (YYYY-MM-DD)", required=False),
            OpenApiParameter(name="min_total_amount", type=float, description="Min total amount", required=False),
            OpenApiParameter(name="max_total_amount", type=float, description="Max total amount", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: DebtListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single debt (if id provided) or a paginated list of debts."
    )
    def get(self, request, id=None):
        """Retrieve single debt or list all debts."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                debt = DebtService.get_by_id(id, include_deleted)
                if not debt:
                    return _error(
                        data={"detail": "Debt not found."},
                        message="Debt not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = DebtReadSerializer(debt, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="Debt",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Debt retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'search': request.query_params.get('search'),
                'status': request.query_params.get('status'),
                'borrower_id': request.query_params.get('borrower_id'),
                'due_date_from': request.query_params.get('due_date_from'),
                'due_date_to': request.query_params.get('due_date_to'),
                'min_total_amount': request.query_params.get('min_total_amount'),
                'max_total_amount': request.query_params.get('max_total_amount'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            filters = {k: v for k, v in filters.items() if v is not None}

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'due_date')
            sort_order = request.query_params.get('sort_order', 'asc')

            result = DebtService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Debts retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Debt",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Debt retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /debts/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Debts"],
        request=DebtCreateSerializer,
        responses={
            201: DebtCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new debt. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DebtCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="Debt",
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
            debt = DebtService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = DebtReadSerializer(debt, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Debt created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Debt creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /debts/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Debts"],
        request=DebtUpdateSerializer,
        responses={
            200: DebtUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing debt. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debt = DebtService.get_by_id(id)
        if not debt:
            return _error(
                data={"detail": "Debt not found."},
                message="Debt not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DebtUpdateSerializer(
            debt,
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
            updated = DebtService.update(
                debt_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = DebtReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Debt updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Debt update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /debts/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Debts"],
        request=DebtUpdateSerializer,
        responses={
            200: DebtUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing debt. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debt = DebtService.get_by_id(id)
        if not debt:
            return _error(
                data={"detail": "Debt not found."},
                message="Debt not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DebtUpdateSerializer(
            debt,
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
            updated = DebtService.update(
                debt_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = DebtReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Debt updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Debt partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /debts/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Debts"],
        responses={
            204: DebtDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a debt. Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to delete debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debt = DebtService.get_by_id(id)
        if not debt:
            return _error(
                data={"detail": "Debt not found."},
                message="Debt not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            DebtService.delete(
                debt_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Debt deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Debt deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Debt Statistics View
# ----------------------------------------------------------------------

class DebtStatisticsView(APIView):
    """
    Get debt statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        responses={
            200: inline_serializer(
                name="DebtStatisticsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get debt statistics including totals by status."
    )
    def get(self, request):
        """Get debt statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view debt statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = DebtService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="Debt",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Debt statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Debt statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve debt statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Debt Aging Summary View
# ----------------------------------------------------------------------

class DebtAgingSummaryView(APIView):
    """
    Get aging summary for accounts receivable.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        parameters=[
            OpenApiParameter(
                name="as_of_date",
                type=str,
                description="Date to calculate aging (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: inline_serializer(
                name="AgingSummaryResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get aging summary for accounts receivable (0-30, 31-60, 61-90, 90+ days)."
    )
    def get(self, request):
        """Get aging summary."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view aging summary."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            as_of_date = request.query_params.get('as_of_date')
            summary = DebtService.get_aging_summary(as_of_date)

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Debt",
                object_id="aging_summary",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=summary,
                message="Aging summary retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Aging summary error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve aging summary.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Debt Collection Schedule View
# ----------------------------------------------------------------------

class DebtCollectionScheduleView(APIView):
    """
    Get collection schedule for debts.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        parameters=[
            OpenApiParameter(
                name="period_type",
                type=str,
                description="Period type: weekly, monthly, semi-annual, yearly",
                required=False,
                default="monthly",
            ),
            OpenApiParameter(
                name="as_of_date",
                type=str,
                description="Reference date (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: inline_serializer(
                name="CollectionScheduleResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get collection schedule grouped by period for active debts."
    )
    def get(self, request):
        """Get collection schedule."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view collection schedule."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            period_type = request.query_params.get('period_type', 'monthly')
            as_of_date = request.query_params.get('as_of_date')

            schedule = DebtService.get_collection_schedule(period_type, as_of_date)

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Debt",
                object_id="collection_schedule",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=schedule,
                message="Collection schedule retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Collection schedule error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve collection schedule.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )