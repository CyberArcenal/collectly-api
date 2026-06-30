import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from borrowers.models.borrower import Borrower
from borrowers.serializers.borrower import (
    BorrowerReadSerializer,
    BorrowerListSerializer,
    BorrowerCreateSerializer,
    BorrowerUpdateSerializer,
)
from borrowers.services.borrower import BorrowerService
from users.permissions.base import IsAccountActive, is_admin, is_staff, can_read, can_edit
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

class BorrowerListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = BorrowerListSerializer(many=True)


class BorrowerDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = BorrowerReadSerializer()


class BorrowerCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = BorrowerReadSerializer()


class BorrowerUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = BorrowerReadSerializer()


class BorrowerDeleteResponseSerializer(serializers.Serializer):
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

class BorrowerCRUDView(APIView):
    """
    CRUD operations for borrowers.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /borrowers/  (list) and GET /borrowers/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Borrowers"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="search", type=str, description="Search by name, email, contact", required=False),
            OpenApiParameter(name="name", type=str, description="Filter by name", required=False),
            OpenApiParameter(name="email", type=str, description="Filter by email", required=False),
            OpenApiParameter(name="contact", type=str, description="Filter by contact", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: BorrowerListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single borrower (if id provided) or a paginated list of borrowers."
    )
    def get(self, request, id=None):
        """Retrieve single borrower or list all borrowers."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Permission check
        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                borrower = BorrowerService.get_by_id(id, include_deleted)
                if not borrower:
                    return _error(
                        data={"detail": "Borrower not found."},
                        message="Borrower not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = BorrowerReadSerializer(borrower, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="Borrower",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Borrower retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'search': request.query_params.get('search'),
                'name': request.query_params.get('name'),
                'email': request.query_params.get('email'),
                'contact': request.query_params.get('contact'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            # Remove None values
            filters = {k: v for k, v in filters.items() if v is not None}

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'name')
            sort_order = request.query_params.get('sort_order', 'asc')

            result = BorrowerService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Borrowers retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Borrower",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Borrower retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /borrowers/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Borrowers"],
        request=BorrowerCreateSerializer,
        responses={
            201: BorrowerCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new borrower. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BorrowerCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="Borrower",
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
            borrower = BorrowerService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = BorrowerReadSerializer(borrower, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Borrower created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Borrower creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /borrowers/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Borrowers"],
        request=BorrowerUpdateSerializer,
        responses={
            200: BorrowerUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing borrower. Admin/Staff only."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        borrower = BorrowerService.get_by_id(id)
        if not borrower:
            return _error(
                data={"detail": "Borrower not found."},
                message="Borrower not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = BorrowerUpdateSerializer(
            borrower,
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
            updated = BorrowerService.update(
                borrower_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = BorrowerReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Borrower updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Borrower update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /borrowers/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Borrowers"],
        request=BorrowerUpdateSerializer,
        responses={
            200: BorrowerUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing borrower. Admin/Staff only."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        borrower = BorrowerService.get_by_id(id)
        if not borrower:
            return _error(
                data={"detail": "Borrower not found."},
                message="Borrower not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = BorrowerUpdateSerializer(
            borrower,
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
            updated = BorrowerService.update(
                borrower_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = BorrowerReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Borrower updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Borrower partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /borrowers/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Borrowers"],
        responses={
            204: BorrowerDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a borrower. Admin only."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to delete borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        borrower = BorrowerService.get_by_id(id)
        if not borrower:
            return _error(
                data={"detail": "Borrower not found."},
                message="Borrower not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            BorrowerService.delete(
                borrower_id=id,
                user=user,
                request=request
            )

            return _success(
                data=None,
                message="Borrower deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Borrower deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )