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
            data = DebtListSerializer(result['data'], many=True, context={'request': request}).data
            response = paginator.get_paginated_response(
                data=data,
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
            
            

# Add these imports at the top if not already present
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError
import datetime

# ===================================================================
# DEBT RESTORE VIEW
# ===================================================================

class DebtRestoreView(APIView):
    """
    Restore a soft-deleted debt. Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        responses={
            200: DebtDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted debt. Admin only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to restore debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            debt = DebtService.restore(
                debt_id=id,
                user=user,
                request=request
            )

            serializer = DebtReadSerializer(debt, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="Debt",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Debt restored successfully.",
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
            logger.exception("Debt restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT PERMANENT DELETE VIEW
# ===================================================================

class DebtPermanentDeleteView(APIView):
    """
    Permanently delete a debt (hard delete). Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        responses={
            204: DebtDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a debt (hard delete). Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to permanently delete debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            DebtService.permanent_delete(
                debt_id=id,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="Debt",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Debt permanently deleted successfully.",
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
            logger.exception("Debt permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete debt.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT BULK CREATE VIEW
# ===================================================================

class DebtBulkCreateView(APIView):
    """
    Bulk create multiple debts. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="BulkCreateRequest",
            fields={
                "debtsArray": serializers.ListField(
                    child=DebtCreateSerializer()
                ),
            }
        ),
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": DebtReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk create multiple debts. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create multiple debts."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debts_data = request.data.get("debtsArray")
        if not isinstance(debts_data, list):
            return _error(
                data={"detail": "debtsArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = DebtService.bulk_create(debts_data, user=user, request=request)
            
            created_serialized = DebtReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_create",
                model_name="Debt",
                object_id="bulk",
                changes={"count": len(result['created']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "created": created_serialized,
                    "errors": result['errors']
                },
                message="Bulk create completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk create failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk create debts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT BULK UPDATE VIEW
# ===================================================================

class DebtBulkUpdateView(APIView):
    """
    Bulk update multiple debts. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="BulkUpdateRequest",
            fields={
                "updatesArray": serializers.ListField(
                    child=serializers.DictField()
                ),
            }
        ),
        responses={
            200: inline_serializer(
                name="BulkUpdateResponse",
                fields={
                    "updated": DebtReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk update multiple debts. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request):
        """Bulk update multiple debts."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        updates = request.data.get("updatesArray")
        if not isinstance(updates, list):
            return _error(
                data={"detail": "updatesArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = DebtService.bulk_update(updates, user=user, request=request)
            
            updated_serialized = DebtReadSerializer(
                result['updated'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_update",
                model_name="Debt",
                object_id="bulk",
                changes={"count": len(result['updated']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "updated": updated_serialized,
                    "errors": result['errors']
                },
                message="Bulk update completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk update debts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT CORRECT TOTAL AMOUNT VIEW
# ===================================================================

class DebtCorrectTotalAmountView(APIView):
    """
    Correct total amount (data entry correction only). Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="CorrectTotalAmountRequest",
            fields={
                "newTotalAmount": serializers.DecimalField(max_digits=12, decimal_places=2),
            }
        ),
        responses={
            200: DebtDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Correct total amount (data entry correction only). Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Correct total amount (no forgiveness flow)."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to correct debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        new_total_amount = request.data.get("newTotalAmount")
        if new_total_amount is None:
            return _error(
                data={"detail": "newTotalAmount is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            debt = DebtService.correct_total_amount(
                debt_id=id,
                new_total_amount=new_total_amount,
                user=user,
                request=request
            )

            serializer = DebtReadSerializer(debt, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="correct_total_amount",
                model_name="Debt",
                object_id=str(id),
                changes={"new_total_amount": new_total_amount},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Debt total amount corrected successfully.",
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
            logger.exception("Correct total amount failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to correct total amount.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT RECALCULATE REMAINING VIEW
# ===================================================================

class DebtRecalculateRemainingView(APIView):
    """
    Recalculate remaining amount based on paidAmount. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        responses={
            200: DebtDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Recalculate remaining amount based on paidAmount. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Recalculate remaining amount."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to recalculate debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            debt = DebtService.recalculate_remaining(
                debt_id=id,
                user=user,
                request=request
            )

            serializer = DebtReadSerializer(debt, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="recalculate_remaining",
                model_name="Debt",
                object_id=str(id),
                changes={"new_remaining": debt.remaining_amount},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Remaining amount recalculated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Recalculate remaining failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to recalculate remaining amount.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT APPLY FORGIVENESS VIEW
# ===================================================================

class DebtApplyForgivenessView(APIView):
    """
    Apply forgiveness to a debt. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="ApplyForgivenessRequest",
            fields={
                "amountForgiven": serializers.DecimalField(max_digits=12, decimal_places=2),
                "reason": serializers.CharField(required=False, allow_null=True),
            }
        ),
        responses={
            200: DebtDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Apply forgiveness to a debt. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
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

        amount_forgiven = request.data.get("amountForgiven")
        if amount_forgiven is None:
            return _error(
                data={"detail": "amountForgiven is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason")

        try:
            debt = DebtService.apply_forgiveness(
                debt_id=id,
                amount_forgiven=amount_forgiven,
                user=user,
                request=request,
                reason=reason
            )

            serializer = DebtReadSerializer(debt, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="apply_forgiveness",
                model_name="Debt",
                object_id=str(id),
                changes={"amount_forgiven": amount_forgiven, "reason": reason},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Forgiveness applied successfully.",
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
            logger.exception("Apply forgiveness failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to apply forgiveness.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT DEBTS IN BUCKET VIEW
# ===================================================================

class DebtDebtsInBucketView(APIView):
    """
    Get debts in a specific aging bucket with pagination.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    @extend_schema(
        tags=["Debts"],
        parameters=[
            OpenApiParameter(name="bucketRange", type=str, description="e.g., '0-30 days'", required=True),
            OpenApiParameter(name="asOfDate", type=str, description="Date to calculate aging (YYYY-MM-DD)", required=True),
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="limit", type=int, description="Items per page", required=False),
        ],
        responses={
            200: DebtListResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get debts in a specific aging bucket with pagination."
    )
    def get(self, request):
        """Get debts in a specific aging bucket."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        bucket_range = request.query_params.get('bucketRange')
        as_of_date = request.query_params.get('asOfDate')
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 10))

        if not bucket_range:
            return _error(
                data={"detail": "bucketRange is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not as_of_date:
            return _error(
                data={"detail": "asOfDate is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = DebtService.get_debts_in_bucket(
                bucket_range=bucket_range,
                as_of_date=as_of_date,
                page=page,
                limit=limit
            )

            paginator = self.pagination_class()
            data = DebtListSerializer(result['data'], many=True, context={'request': request}).data
            response = paginator.get_paginated_response(
                data=data,
                message="Debts in bucket retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Debt",
                object_id="bucket",
                changes={"bucket_range": bucket_range, "as_of_date": as_of_date},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Debts in bucket retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT MARK PERIOD PAID VIEW
# ===================================================================

class DebtMarkPeriodPaidView(APIView):
    """
    Mark all debts for a borrower in a period as paid.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="MarkPeriodPaidRequest",
            fields={
                "borrowerId": serializers.IntegerField(),
                "periodType": serializers.ChoiceField(choices=['weekly', 'monthly', 'semi-annual', 'yearly']),
                "paymentDate": serializers.DateField(),
                "methodId": serializers.IntegerField(),
            }
        ),
        responses={
            200: inline_serializer(
                name="MarkPeriodPaidResponse",
                fields={
                    "payments": serializers.ListField(child=serializers.DictField()),
                    "count": serializers.IntegerField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Mark all debts for a borrower in a period as paid."
    )
    @transaction.atomic
    def post(self, request):
        """Mark period paid."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to mark periods as paid."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        borrower_id = request.data.get("borrowerId")
        period_type = request.data.get("periodType")
        payment_date = request.data.get("paymentDate")
        method_id = request.data.get("methodId")

        if not borrower_id:
            return _error(
                data={"detail": "borrowerId is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not period_type:
            return _error(
                data={"detail": "periodType is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not payment_date:
            return _error(
                data={"detail": "paymentDate is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not method_id:
            return _error(
                data={"detail": "methodId is required."},
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = DebtService.mark_period_paid(
                borrower_id=borrower_id,
                period_type=period_type,
                payment_date=payment_date,
                method_id=method_id,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="mark_period_paid",
                model_name="Debt",
                object_id="period",
                changes={
                    "borrower_id": borrower_id,
                    "period_type": period_type,
                    "count": result['count'],
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "payments": result['payments'],
                    "count": result['count'],
                },
                message=f"{result['count']} debts marked as paid successfully.",
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
            logger.exception("Mark period paid failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to mark period as paid.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT FIX PRECISION VIEW
# ===================================================================

class DebtFixPrecisionView(APIView):
    """
    Fix floating point precision for debts. Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="FixPrecisionRequest",
            fields={
                "debtId": serializers.IntegerField(required=False),
            }
        ),
        responses={
            200: inline_serializer(
                name="FixPrecisionResponse",
                fields={
                    "fixed": serializers.IntegerField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Fix floating point precision for debts. Admin only."
    )
    @transaction.atomic
    def post(self, request):
        """Fix floating point precision."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to fix precision."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debt_id = request.data.get("debtId")

        try:
            result = DebtService.fix_precision(debt_id=debt_id)

            log_audit_event(
                request=request,
                user=user,
                action_type="fix_precision",
                model_name="Debt",
                object_id=debt_id or "all",
                changes={"fixed": result['fixed']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"fixed": result['fixed']},
                message=f"Fixed precision for {result['fixed']} debts.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Fix precision failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to fix precision.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT IMPORT VIEW
# ===================================================================

class DebtImportView(APIView):
    """
    Import debts from CSV content. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="ImportRequest",
            fields={
                "fileContent": serializers.CharField(),
                "fileName": serializers.CharField(required=False),
            }
        ),
        responses={
            201: inline_serializer(
                name="ImportResponse",
                fields={
                    "imported": DebtReadSerializer(many=True),
                    "errors": serializers.ListField(
                        child=serializers.DictField()
                    )
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Import debts from CSV content. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Import debts from CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to import debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        file_content = request.data.get("fileContent")
        if not file_content:
            return _error(
                data={"detail": "fileContent is required."},
                message="Invalid request.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import csv
            from io import StringIO
            reader = csv.DictReader(StringIO(file_content))
            debts_data = list(reader)
        except Exception as e:
            return _error(
                data={"detail": f"Invalid CSV: {str(e)}"},
                message="CSV parsing error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = DebtService.bulk_create(debts_data, user=user, request=request)
            
            imported_serialized = DebtReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="import_csv",
                model_name="Debt",
                object_id="import",
                changes={"count": len(result['created']), "errors": len(result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "imported": imported_serialized,
                    "errors": result['errors']
                },
                message="CSV import completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("CSV import failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to import debts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# DEBT EXPORT VIEW
# ===================================================================

class DebtExportView(APIView):
    """
    Export debts to CSV or JSON.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Debts"],
        request=inline_serializer(
            name="ExportRequest",
            fields={
                "format": serializers.ChoiceField(choices=["csv", "json"], default="json"),
                "filters": serializers.DictField(required=False),
            }
        ),
        responses={
            200: inline_serializer(
                name="ExportResponse",
                fields={
                    "format": serializers.CharField(),
                    "data": serializers.CharField(help_text="CSV string or JSON array"),
                    "filename": serializers.CharField()
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Export debts to CSV or JSON."
    )
    def post(self, request):
        """Export debts."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to export debts."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.data.get("format", "json")
        filters = request.data.get("filters", {})

        try:
            exported_data = DebtService.export_debts(filters)
            
            if fmt == "csv":
                import csv
                from io import StringIO
                output = StringIO()
                if exported_data:
                    fieldnames = exported_data[0].keys()
                    writer = csv.DictWriter(output, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(exported_data)
                data_str = output.getvalue()
                filename = f"debts_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # json
                import json
                data_str = json.dumps(exported_data, default=str)
                filename = f"debts_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"

            log_audit_event(
                request=request,
                user=user,
                action_type="export",
                model_name="Debt",
                object_id="export",
                changes={"format": fmt, "count": len(exported_data)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "format": fmt,
                    "data": data_str,
                    "filename": filename
                },
                message="Export completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Export failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to export debts.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )