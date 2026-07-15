import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from payment_methods.models.payment_method import PaymentMethod
from payment_methods.serializers.payment_method import (
    PaymentMethodReadSerializer,
    PaymentMethodListSerializer,
    PaymentMethodCreateSerializer,
    PaymentMethodUpdateSerializer,
    PaymentMethodSetDefaultSerializer,
    PaymentMethodStatsSerializer,
)
from payment_methods.services.payment_method import PaymentMethodService
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
class PaymentMethodStatsItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    icon = serializers.CharField()
    is_default = serializers.BooleanField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.FloatField()
    average_transaction = serializers.FloatField()


class PaymentMethodDefaultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    icon = serializers.CharField()


class PaymentMethodOverallStatsDataSerializer(serializers.Serializer):
    total_methods = serializers.IntegerField()
    total_transactions = serializers.IntegerField()
    total_amount_collected = serializers.FloatField()
    default_method = PaymentMethodDefaultSerializer(allow_null=True)
    methods = PaymentMethodStatsItemSerializer(many=True)


class PaymentMethodOverallStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodOverallStatsDataSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)
    
class PaymentMethodListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = PaymentMethodListSerializer(many=True)


class PaymentMethodDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodReadSerializer()


class PaymentMethodCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodReadSerializer()


class PaymentMethodUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodReadSerializer()


class PaymentMethodDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class PaymentMethodSetDefaultResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodReadSerializer()


class PaymentMethodStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentMethodStatsSerializer()


class PaymentMethodAllStatsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.ListField(child=serializers.DictField())


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class PaymentMethodCRUDView(APIView):
    """
    CRUD operations for payment methods.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /payment-methods/  (list) and GET /payment-methods/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payment Methods"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: PaymentMethodListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single payment method (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single payment method or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view payment methods."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                method = PaymentMethodService.get_by_id(id)
                if not method:
                    return _error(
                        data={"detail": "Payment method not found."},
                        message="Payment method not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # If include_deleted is false and method is deleted, return not found
                if not include_deleted and method.deleted_at:
                    return _error(
                        data={"detail": "Payment method not found."},
                        message="Payment method not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = PaymentMethodReadSerializer(method, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="PaymentMethod",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Payment method retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))

            result = PaymentMethodService.get_list(
                page=page,
                limit=limit
            )

            # Filter out deleted if not included
            if not include_deleted:
                result['data'] = [m for m in result['data'] if not m.deleted_at]

            paginator = self.pagination_class()
            serialized_data = PaymentMethodListSerializer(
                result['data'],
                many=True,
                context={'request': request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Payment methods retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="PaymentMethod",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Payment method retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /payment-methods/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payment Methods"],
        request=PaymentMethodCreateSerializer,
        responses={
            201: PaymentMethodCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new payment method. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new payment method."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create payment methods."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PaymentMethodCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="PaymentMethod",
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
            method = PaymentMethodService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PaymentMethodReadSerializer(method, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment method created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment method creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create payment method.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /payment-methods/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payment Methods"],
        request=PaymentMethodUpdateSerializer,
        responses={
            200: PaymentMethodUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing payment method. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a payment method."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update payment methods."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        method = PaymentMethodService.get_by_id(id)
        if not method:
            return _error(
                data={"detail": "Payment method not found."},
                message="Payment method not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentMethodUpdateSerializer(
            method,
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
            updated = PaymentMethodService.update(
                method_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PaymentMethodReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment method updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment method update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update payment method.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /payment-methods/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payment Methods"],
        request=PaymentMethodUpdateSerializer,
        responses={
            200: PaymentMethodUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing payment method. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a payment method."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update payment methods."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        method = PaymentMethodService.get_by_id(id)
        if not method:
            return _error(
                data={"detail": "Payment method not found."},
                message="Payment method not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentMethodUpdateSerializer(
            method,
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
            updated = PaymentMethodService.update(
                method_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PaymentMethodReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment method updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment method partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update payment method.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /payment-methods/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payment Methods"],
        responses={
            204: PaymentMethodDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a payment method. Admin/Staff only. Cannot delete default payment method."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a payment method."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to delete payment methods."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        method = PaymentMethodService.get_by_id(id)
        if not method:
            return _error(
                data={"detail": "Payment method not found."},
                message="Payment method not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            PaymentMethodService.delete(
                method_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Payment method deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment method deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete payment method.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Payment Method Set Default View
# ----------------------------------------------------------------------

class PaymentMethodSetDefaultView(APIView):
    """
    Set a payment method as default.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payment Methods"],
        request=PaymentMethodSetDefaultSerializer,
        responses={
            200: PaymentMethodSetDefaultResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Set a payment method as default. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Set a payment method as default."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to set default payment method."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        method = PaymentMethodService.get_by_id(id)
        if not method:
            return _error(
                data={"detail": "Payment method not found."},
                message="Payment method not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentMethodSetDefaultSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.instance = method

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated = PaymentMethodService.set_default(
                method_id=id,
                user=user,
                request=request
            )

            read_serializer = PaymentMethodReadSerializer(updated, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="payment_method_set_default",
                model_name="PaymentMethod",
                object_id=str(id),
                changes={"is_default": True},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Payment method set as default successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Set default payment method failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to set default payment method.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Payment Method Stats View (Single)
# ----------------------------------------------------------------------

class PaymentMethodStatsView(APIView):
    """
    Get statistics for a specific payment method.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payment Methods"],
        responses={
            200: PaymentMethodStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get statistics for a specific payment method."
    )
    def get(self, request, id):
        """Get statistics for a payment method."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view payment method statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            method = PaymentMethodService.get_by_id(id)
            if not method:
                return _error(
                    data={"detail": "Payment method not found."},
                    message="Payment method not found.",
                    status=status.HTTP_404_NOT_FOUND,
                )

            stats = PaymentMethodService.get_stats(id)

            serializer = PaymentMethodStatsSerializer(stats, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="PaymentMethodStat",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Payment method statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Payment method stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve payment method statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
         

# ============================================================
# Payment Method All Stats View (UPDATED)
# ============================================================

class PaymentMethodAllStatsView(APIView):
    """
    Get overall summary statistics for all payment methods.

    Returns aggregated totals and per-method breakdown including:
    - total number of payment methods
    - total transactions across all methods
    - total amount collected across all methods
    - default payment method info
    - detailed stats for each method
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payment Methods"],
        summary="Get overall payment method statistics",
        description="Returns aggregated totals and per-method breakdown for all payment methods.",
        responses={
            200: PaymentMethodOverallStatsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                name="Success Response",
                value={
                    "status": True,
                    "message": "Payment method statistics retrieved successfully.",
                    "data": {
                        "total_methods": 4,
                        "total_transactions": 1250,
                        "total_amount_collected": 875000.00,
                        "default_method": {
                            "id": 1,
                            "name": "Cash",
                            "icon": "DollarSign"
                        },
                        "methods": [
                            {
                                "id": 1,
                                "name": "Cash",
                                "icon": "DollarSign",
                                "is_default": True,
                                "transaction_count": 800,
                                "total_amount": 500000.00,
                                "average_transaction": 625.00
                            },
                            {
                                "id": 2,
                                "name": "Bank Transfer",
                                "icon": "Landmark",
                                "is_default": False,
                                "transaction_count": 300,
                                "total_amount": 300000.00,
                                "average_transaction": 1000.00
                            },
                            {
                                "id": 3,
                                "name": "GCash",
                                "icon": "Smartphone",
                                "is_default": False,
                                "transaction_count": 150,
                                "total_amount": 75000.00,
                                "average_transaction": 500.00
                            }
                        ]
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
                value={"status": False, "message": "You do not have permission to view payment method statistics.", "data": None},
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request):
        """Get statistics for all payment methods."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view payment method statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = PaymentMethodService.get_overall_summary()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="PaymentMethodStat",
                object_id="all",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Payment method statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("All payment methods stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve payment method statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )