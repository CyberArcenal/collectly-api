import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from audit.utils.log import log_audit_event
from payments.models.penalty_transaction import PenaltyTransaction
from payments.serializers.penalty_transaction import (
    PenaltyTransactionReadSerializer,
    PenaltyTransactionListSerializer,
    PenaltyTransactionCreateSerializer,
    PenaltyTransactionUpdateSerializer,
)
from payments.services.penalty_transaction import PenaltyTransactionService
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

class PenaltyTransactionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = PenaltyTransactionListSerializer(many=True)


class PenaltyTransactionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PenaltyTransactionReadSerializer()


class PenaltyTransactionCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PenaltyTransactionReadSerializer()


class PenaltyTransactionUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PenaltyTransactionReadSerializer()


class PenaltyTransactionDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class PenaltyTransactionStatisticsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField()


class PenaltyTransactionAutoRunResponseSerializer(serializers.Serializer):
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

class PenaltyTransactionCRUDView(APIView):
    """
    CRUD operations for penalty transactions.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /penalties/  (list) and GET /penalties/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Penalties"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="debt_id", type=int, description="Filter by debt ID", required=False),
            OpenApiParameter(name="borrower_id", type=int, description="Filter by borrower ID", required=False),
            OpenApiParameter(name="penalty_date_from", type=str, description="Penalty date from", required=False),
            OpenApiParameter(name="penalty_date_to", type=str, description="Penalty date to", required=False),
            OpenApiParameter(name="min_amount", type=float, description="Min amount", required=False),
            OpenApiParameter(name="max_amount", type=float, description="Max amount", required=False),
            OpenApiParameter(name="reason", type=str, description="Filter by reason", required=False),
            OpenApiParameter(name="is_auto", type=bool, description="Filter by auto-generated", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: PenaltyTransactionListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single penalty (if id provided) or a paginated list of penalties."
    )
    def get(self, request, id=None):
        """Retrieve single penalty or list all penalties."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                penalty = PenaltyTransactionService.get_by_id(id, include_deleted)
                if not penalty:
                    return _error(
                        data={"detail": "Penalty not found."},
                        message="Penalty not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = PenaltyTransactionReadSerializer(penalty, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="PenaltyTransaction",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Penalty retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'debt_id': request.query_params.get('debt_id'),
                'borrower_id': request.query_params.get('borrower_id'),
                'penalty_date_from': request.query_params.get('penalty_date_from'),
                'penalty_date_to': request.query_params.get('penalty_date_to'),
                'min_amount': request.query_params.get('min_amount'),
                'max_amount': request.query_params.get('max_amount'),
                'reason': request.query_params.get('reason'),
                'is_auto': request.query_params.get('is_auto'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            filters = {k: v for k, v in filters.items() if v is not None}

            # Convert is_auto to boolean
            if filters.get('is_auto') is not None:
                filters['is_auto'] = filters['is_auto'].lower() == 'true'

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'penalty_date')
            sort_order = request.query_params.get('sort_order', 'desc')

            result = PenaltyTransactionService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Penalties retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="PenaltyTransaction",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Penalty retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /penalties/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Penalties"],
        request=PenaltyTransactionCreateSerializer,
        responses={
            201: PenaltyTransactionCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new penalty transaction. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PenaltyTransactionCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="PenaltyTransaction",
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
            penalty = PenaltyTransactionService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PenaltyTransactionReadSerializer(penalty, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Penalty created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Penalty creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /penalties/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Penalties"],
        request=PenaltyTransactionUpdateSerializer,
        responses={
            200: PenaltyTransactionUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing penalty. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        penalty = PenaltyTransactionService.get_by_id(id)
        if not penalty:
            return _error(
                data={"detail": "Penalty not found."},
                message="Penalty not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PenaltyTransactionUpdateSerializer(
            penalty,
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
            updated = PenaltyTransactionService.update(
                penalty_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PenaltyTransactionReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Penalty updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Penalty update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /penalties/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Penalties"],
        request=PenaltyTransactionUpdateSerializer,
        responses={
            200: PenaltyTransactionUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing penalty. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        penalty = PenaltyTransactionService.get_by_id(id)
        if not penalty:
            return _error(
                data={"detail": "Penalty not found."},
                message="Penalty not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PenaltyTransactionUpdateSerializer(
            penalty,
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
            updated = PenaltyTransactionService.update(
                penalty_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = PenaltyTransactionReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Penalty updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Penalty partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /penalties/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Penalties"],
        responses={
            204: PenaltyTransactionDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a penalty. Admin/Staff only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to delete penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        penalty = PenaltyTransactionService.get_by_id(id)
        if not penalty:
            return _error(
                data={"detail": "Penalty not found."},
                message="Penalty not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            PenaltyTransactionService.delete(
                penalty_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Penalty deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Penalty deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Penalty Transaction Statistics View
# ----------------------------------------------------------------------

class PenaltyTransactionStatisticsView(APIView):
    """
    Get penalty transaction statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        responses={
            200: PenaltyTransactionStatisticsResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get penalty statistics including totals by type (auto vs manual) and date ranges."
    )
    def get(self, request):
        """Get penalty statistics."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view penalty statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = PenaltyTransactionService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="PenaltyTransaction",
                object_id="stats",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=stats,
                message="Penalty statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Penalty statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve penalty statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Penalty Transaction Auto Run View
# ----------------------------------------------------------------------

class PenaltyTransactionAutoRunView(APIView):
    """
    Run auto-penalty for overdue debts.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        responses={
            200: PenaltyTransactionAutoRunResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Run auto-penalty for overdue debts. Admin only."
    )
    @transaction.atomic
    def post(self, request):
        """Run auto-penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to run auto-penalty."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = PenaltyTransactionService.run_auto_penalties()

            log_audit_event(
                request=request,
                user=user,
                action_type="penalty_auto_run",
                model_name="PenaltyTransaction",
                object_id="auto",
                changes={"processed": result['processed'], "errors": result['errors']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message=f"Auto-penalty completed: {result['processed']} processed, {result['errors']} errors.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Auto-penalty run failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to run auto-penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
            
        
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers
from django.core.exceptions import ValidationError

# ===================================================================
# PENALTY RESTORE VIEW
# ===================================================================

class PenaltyRestoreView(APIView):
    """
    Restore a soft-deleted penalty. Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        responses={
            200: PenaltyTransactionDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted penalty. Admin only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to restore penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            penalty = PenaltyTransactionService.restore(
                penalty_id=id,
                user=user,
                request=request
            )

            serializer = PenaltyTransactionReadSerializer(penalty, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="PenaltyTransaction",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Penalty restored successfully.",
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
            logger.exception("Penalty restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY PERMANENT DELETE VIEW
# ===================================================================

class PenaltyPermanentDeleteView(APIView):
    """
    Permanently delete a penalty (hard delete). Admin only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        responses={
            204: PenaltyTransactionDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a penalty (hard delete). Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a penalty."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to permanently delete penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            PenaltyTransactionService.permanent_delete(
                penalty_id=id,
                user=user,
                request=request
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="PenaltyTransaction",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Penalty permanently deleted successfully.",
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
            logger.exception("Penalty permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY BULK CREATE VIEW
# ===================================================================

class PenaltyBulkCreateView(APIView):
    """
    Bulk create multiple penalties. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        request=inline_serializer(
            name="BulkCreateRequest",
            fields={
                "penaltiesArray": serializers.ListField(
                    child=PenaltyTransactionCreateSerializer()
                ),
            }
        ),
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": PenaltyTransactionReadSerializer(many=True),
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
        description="Bulk create multiple penalties. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create multiple penalties."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        penalties_data = request.data.get("penaltiesArray")
        if not isinstance(penalties_data, list):
            return _error(
                data={"detail": "penaltiesArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = PenaltyTransactionService.bulk_create(
                penalties_data, 
                user=user, 
                request=request
            )
            
            created_serialized = PenaltyTransactionReadSerializer(
                result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_create",
                model_name="PenaltyTransaction",
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
                message="Failed to bulk create penalties.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY BULK UPDATE VIEW
# ===================================================================

class PenaltyBulkUpdateView(APIView):
    """
    Bulk update multiple penalties. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
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
                    "updated": PenaltyTransactionReadSerializer(many=True),
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
        description="Bulk update multiple penalties. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request):
        """Bulk update multiple penalties."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update penalties."},
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
            result = PenaltyTransactionService.bulk_update(
                updates, 
                user=user, 
                request=request
            )
            
            updated_serialized = PenaltyTransactionReadSerializer(
                result['updated'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_update",
                model_name="PenaltyTransaction",
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
                message="Failed to bulk update penalties.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY IMPORT VIEW
# ===================================================================

class PenaltyImportView(APIView):
    """
    Import penalties from CSV content. Admin/Staff only.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
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
                    "imported": PenaltyTransactionReadSerializer(many=True),
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
        description="Import penalties from CSV content. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Import penalties from CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to import penalties."},
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
            penalties_data = list(reader)
        except Exception as e:
            return _error(
                data={"detail": f"Invalid CSV: {str(e)}"},
                message="CSV parsing error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Use bulk_create with parsed data
            bulk_result = PenaltyTransactionService.bulk_create(
                penalties_data, 
                user=user, 
                request=request
            )
            
            imported_serialized = PenaltyTransactionReadSerializer(
                bulk_result['created'], 
                many=True, 
                context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="import_csv",
                model_name="PenaltyTransaction",
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
                message="Failed to import penalties.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY EXPORT VIEW
# ===================================================================

class PenaltyExportView(APIView):
    """
    Export penalties to CSV or JSON.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
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
        description="Export penalties to CSV or JSON."
    )
    def post(self, request):
        """Export penalties."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to export penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.data.get("format", "json")
        filters = request.data.get("filters", {})

        try:
            exported_data = PenaltyTransactionService.export_penalties(filters)
            
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
                filename = f"penalties_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # json
                import json
                data_str = json.dumps(exported_data, default=str)
                filename = f"penalties_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"

            log_audit_event(
                request=request,
                user=user,
                action_type="export",
                model_name="PenaltyTransaction",
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
                message="Failed to export penalties.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PENALTY TOTAL BY DEBT VIEW
# ===================================================================

class PenaltyTotalByDebtView(APIView):
    """
    Get total penalty amount for a specific debt.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Penalties"],
        parameters=[
            OpenApiParameter(
                name="debt_id",
                type=int,
                description="ID of the debt",
                required=True,
            ),
            OpenApiParameter(
                name="include_deleted",
                type=bool,
                description="Include soft-deleted penalties",
                required=False,
            ),
        ],
        responses={
            200: inline_serializer(
                name="TotalPenaltyForDebtResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.DictField(),
                }
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get total penalty amount and count for a specific debt."
    )
    def get(self, request):
        """Get total penalty for a debt."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view penalties."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        debt_id = request.query_params.get('debt_id')
        if not debt_id:
            return _error(
                data={"detail": "debt_id is required."},
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
            
            result = PenaltyTransactionService.get_total_penalty_for_debt(
                debt_id=int(debt_id),
                include_deleted=include_deleted
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="PenaltyTransaction",
                object_id="total_by_debt",
                changes={"debt_id": debt_id},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=result,
                message="Total penalty retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Total penalty retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve total penalty.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )