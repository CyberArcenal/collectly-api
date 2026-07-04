import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from decimal import Decimal

from audit.utils.log import log_audit_event
from payments.models.payment_transaction import PaymentTransaction
from payments.serializers.payment_transaction import (
    PaymentTransactionReadSerializer,
    PaymentTransactionListSerializer,
    PaymentTransactionCreateSerializer,
    PaymentTransactionUpdateSerializer,
    PaymentTransactionVoidSerializer,
)
from payments.services.payment_transaction import PaymentTransactionService
from users.permissions.base import IsAccountActive, can_read, can_edit, is_admin
from utils.response import BasePaginatedSerializer, CustomPagination, _success, _error
from utils.security import get_client_ip

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)
from django.utils import timezone
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Response serializers for documentation
# ----------------------------------------------------------------------

class PaymentTransactionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = PaymentTransactionListSerializer(many=True)


class PaymentTransactionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentTransactionReadSerializer()


class PaymentTransactionCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentTransactionReadSerializer()


class PaymentTransactionUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentTransactionReadSerializer()


class PaymentTransactionDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class PaymentTransactionVoidResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PaymentTransactionReadSerializer()


class PaymentTransactionStatisticsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class PaymentCollectionReportResponseSerializer(serializers.Serializer):
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

class PaymentTransactionCRUDView(APIView):
    """
    CRUD operations for payment transactions.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /payments/  (list) and GET /payments/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payments"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="debt_id", type=int, description="Filter by debt ID", required=False),
            OpenApiParameter(name="borrower_id", type=int, description="Filter by borrower ID", required=False),
            OpenApiParameter(name="method_id", type=int, description="Filter by payment method ID", required=False),
            OpenApiParameter(name="reference", type=str, description="Filter by reference", required=False),
            OpenApiParameter(name="payment_date_from", type=str, description="Payment date from", required=False),
            OpenApiParameter(name="payment_date_to", type=str, description="Payment date to", required=False),
            OpenApiParameter(name="min_amount", type=float, description="Min amount", required=False),
            OpenApiParameter(name="max_amount", type=float, description="Max amount", required=False),
            OpenApiParameter(name="search", type=str, description="Search by reference or notes", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: PaymentTransactionListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single payment (if id provided) or a paginated list of payments."
    )
    def get(self, request, id=None):
        """Retrieve single payment or list all payments."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                payment = PaymentTransactionService.get_by_id(id, include_deleted)
                if not payment:
                    return _error(
                        data={"detail": "Payment not found."},
                        message="Payment not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = PaymentTransactionReadSerializer(payment, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="PaymentTransaction",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Payment retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'debt_id': request.query_params.get('debt_id'),
                'borrower_id': request.query_params.get('borrower_id'),
                'method_id': request.query_params.get('method_id'),
                'reference': request.query_params.get('reference'),
                'payment_date_from': request.query_params.get('payment_date_from'),
                'payment_date_to': request.query_params.get('payment_date_to'),
                'min_amount': request.query_params.get('min_amount'),
                'max_amount': request.query_params.get('max_amount'),
                'search': request.query_params.get('search'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            filters = {k: v for k, v in filters.items() if v is not None}

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'payment_date')
            sort_order = request.query_params.get('sort_order', 'desc')

            result = PaymentTransactionService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            serialized_data = PaymentTransactionListSerializer(
                result['data'],
                many=True,
                context={'request': request}
            ).data

            response = paginator.get_paginated_response(
                data=serialized_data,
                message="Payments retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="PaymentTransaction",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Payment retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /payments/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payments"],
        request=PaymentTransactionCreateSerializer,
        responses={
            201: PaymentTransactionCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new payment transaction. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PaymentTransactionCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="PaymentTransaction",
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
            # Add recorded_by from current user if not provided
            data = serializer.validated_data
            if not data.get('recorded_by'):
                data['recorded_by'] = user

            payment = PaymentTransactionService.create(
                data=data,
                user=user,
                request=request
            )

            read_serializer = PaymentTransactionReadSerializer(payment, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /payments/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payments"],
        request=PaymentTransactionUpdateSerializer,
        responses={
            200: PaymentTransactionUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing payment. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = PaymentTransactionService.get_by_id(id)
        if not payment:
            return _error(
                data={"detail": "Payment not found."},
                message="Payment not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentTransactionUpdateSerializer(
            payment,
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
            updated = PaymentTransactionService.update(
                payment_id=id,
                data=serializer.validated_data,
                user=user,
                request=request,
                is_admin=is_admin(user)
            )

            read_serializer = PaymentTransactionReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /payments/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payments"],
        request=PaymentTransactionUpdateSerializer,
        responses={
            200: PaymentTransactionUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing payment. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = PaymentTransactionService.get_by_id(id)
        if not payment:
            return _error(
                data={"detail": "Payment not found."},
                message="Payment not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentTransactionUpdateSerializer(
            payment,
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
            updated = PaymentTransactionService.update(
                payment_id=id,
                data=serializer.validated_data,
                user=user,
                request=request,
                is_admin=is_admin(user)
            )

            read_serializer = PaymentTransactionReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Payment updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /payments/<id>/
    # NOT ALLOWED - Use void instead
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Payments"],
        responses={
            405: inline_serializer(
                name="MethodNotAllowedResponse",
                fields={
                    "status": serializers.BooleanField(default=False),
                    "message": serializers.CharField(),
                }
            ),
        },
        description="Deletion of payments is not allowed. Use void instead.",
        exclude=True,
    )
    def delete(self, request, id=None):
        return _error(
            data={"detail": "Payments cannot be deleted directly. Use void endpoint instead."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


# ----------------------------------------------------------------------
# Payment Transaction Void View
# ----------------------------------------------------------------------

class PaymentTransactionVoidView(APIView):
    """
    Void a payment transaction.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        request=PaymentTransactionVoidSerializer,
        responses={
            200: PaymentTransactionVoidResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Void a payment transaction. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Void a payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to void payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        payment = PaymentTransactionService.get_by_id(id)
        if not payment:
            return _error(
                data={"detail": "Payment not found."},
                message="Payment not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PaymentTransactionVoidSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.instance = payment

        if not serializer.is_valid():
            transaction.set_rollback(True)
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            voided = PaymentTransactionService.void_payment(
                payment_id=id,
                user=user,
                request=request
            )

            read_serializer = PaymentTransactionReadSerializer(voided, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="payment_void",
                model_name="PaymentTransaction",
                object_id=str(id),
                changes={"reason": serializer.validated_data.get('reason')},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Payment voided successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Payment void failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to void payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Payment Transaction Statistics View
# ----------------------------------------------------------------------

class PaymentTransactionStatisticsView(APIView):
    """
    Get payment transaction statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        responses={
            200: PaymentTransactionStatisticsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get payment statistics including totals by method and date ranges."
    )
    def get(self, request):
        """Get payment statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view payment statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = PaymentTransactionService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="PaymentTransaction",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Payment statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Payment statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve payment statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Payment Collection Report View
# ----------------------------------------------------------------------

class PaymentCollectionReportView(APIView):
    """
    Get collection report for a date range.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        parameters=[
            OpenApiParameter(
                name="from_date",
                type=str,
                description="Start date (YYYY-MM-DD)",
                required=True,
            ),
            OpenApiParameter(
                name="to_date",
                type=str,
                description="End date (YYYY-MM-DD)",
                required=True,
            ),
            OpenApiParameter(
                name="target",
                type=float,
                description="Expected total collection amount",
                required=True,
            ),
        ],
        responses={
            200: PaymentCollectionReportResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get collection report with actual vs expected collection by date and debtor."
    )
    def get(self, request):
        """Get collection report."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view collection reports."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            from_date = request.query_params.get('from_date')
            to_date = request.query_params.get('to_date')
            target = request.query_params.get('target')

            if not from_date or not to_date or target is None:
                return _error(
                    data={"detail": "from_date, to_date, and target parameters are required."},
                    message="Missing required parameters.",
                    status=status.HTTP_400_BAD_REQUEST,
                )

            report = PaymentTransactionService.get_collection_report(
                from_date=from_date,
                to_date=to_date,
                target=Decimal(str(target))
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="PaymentTransaction",
                object_id="collection_report",
                changes={"from_date": from_date, "to_date": to_date},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=report,
                message="Collection report retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Collection report error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve collection report.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            

from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# PAYMENT RESTORE VIEW
# ===================================================================

class PaymentRestoreView(APIView):
    """
    Restore a soft-deleted payment. Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        responses={
            200: PaymentTransactionDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted payment. Admin only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to restore payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            payment = PaymentTransactionService.restore(
                payment_id=id,
                user=user,
                request=request
            )

            serializer = PaymentTransactionReadSerializer(payment, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="PaymentTransaction",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Payment restored successfully.",
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
            logger.exception("Payment restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PAYMENT PERMANENT DELETE VIEW
# ===================================================================

class PaymentPermanentDeleteView(APIView):
    """
    Permanently delete a payment (hard delete). Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        responses={
            204: PaymentTransactionDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a payment (hard delete). Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a payment."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to permanently delete payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            PaymentTransactionService.permanent_delete(
                payment_id=id,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="PaymentTransaction",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Payment permanently deleted successfully.",
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
            logger.exception("Payment permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete payment.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PAYMENT BULK CREATE VIEW
# ===================================================================

class PaymentBulkCreateView(APIView):
    """
    Bulk create multiple payments. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
        request=inline_serializer(
            name="BulkCreateRequest",
            fields={
                "paymentsArray": serializers.ListField(
                    child=PaymentTransactionCreateSerializer()
                ),
            }
        ),
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": PaymentTransactionReadSerializer(many=True),
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
        description="Bulk create multiple payments. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create multiple payments."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        payments_data = request.data.get("paymentsArray")
        if not isinstance(payments_data, list):
            return _error(
                data={"detail": "paymentsArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = PaymentTransactionService.bulk_create(
                payments_data, 
                user=user, 
                request=request
            )
            
            created_serialized = PaymentTransactionReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_create",
                model_name="PaymentTransaction",
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
                message="Failed to bulk create payments.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PAYMENT BULK UPDATE VIEW
# ===================================================================

class PaymentBulkUpdateView(APIView):
    """
    Bulk update multiple payments. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
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
                    "updated": PaymentTransactionReadSerializer(many=True),
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
        description="Bulk update multiple payments. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request):
        """Bulk update multiple payments."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update payments."},
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
            result = PaymentTransactionService.bulk_update(
                updates, 
                user=user, 
                request=request
            )
            
            updated_serialized = PaymentTransactionReadSerializer(
                result['updated'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_update",
                model_name="PaymentTransaction",
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
                message="Failed to bulk update payments.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PAYMENT IMPORT VIEW
# ===================================================================

class PaymentImportView(APIView):
    """
    Import payments from CSV content. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
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
                    "imported": PaymentTransactionReadSerializer(many=True),
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
        description="Import payments from CSV content. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Import payments from CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to import payments."},
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
            payments_data = list(reader)
        except Exception as e:
            return _error(
                data={"detail": f"Invalid CSV: {str(e)}"},
                message="CSV parsing error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = PaymentTransactionService.import_from_csv(
                file_path=None,
                user=user,
                request=request
            )
            
            # Since import_from_csv expects a file path, we need to handle differently
            # Let's use bulk_create with the parsed data
            bulk_result = PaymentTransactionService.bulk_create(
                payments_data, 
                user=user, 
                request=request
            )
            
            imported_serialized = PaymentTransactionReadSerializer(
                bulk_result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="import_csv",
                model_name="PaymentTransaction",
                object_id="import",
                changes={"count": len(bulk_result['created']), "errors": len(bulk_result['errors'])},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={
                    "imported": imported_serialized,
                    "errors": bulk_result['errors']
                },
                message="CSV import completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("CSV import failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to import payments.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PAYMENT EXPORT VIEW
# ===================================================================

class PaymentExportView(APIView):
    """
    Export payments to CSV or JSON.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Payments"],
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
        description="Export payments to CSV or JSON."
    )
    def post(self, request):
        """Export payments."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to export payments."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.data.get("format", "json")
        filters = request.data.get("filters", {})

        try:
            exported_data = PaymentTransactionService.export_payments(filters)
            
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
                filename = f"payments_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # json
                import json
                data_str = json.dumps(exported_data, default=str)
                filename = f"payments_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"

            log_audit_event(
                request=request,
                user=user,
                action_type="export",
                model_name="PaymentTransaction",
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
                message="Failed to export payments.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )