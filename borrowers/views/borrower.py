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
from users.permissions.base import (
    IsAccountActive,
    is_admin,
    is_staff,
    can_read,
    can_edit,
)
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
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
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
            OpenApiParameter(
                name="search",
                type=str,
                description="Search by name, email, contact",
                required=False,
            ),
            OpenApiParameter(
                name="name", type=str, description="Filter by name", required=False
            ),
            OpenApiParameter(
                name="email", type=str, description="Filter by email", required=False
            ),
            OpenApiParameter(
                name="contact",
                type=str,
                description="Filter by contact",
                required=False,
            ),
            OpenApiParameter(
                name="include_deleted",
                type=bool,
                description="Include soft-deleted",
                required=False,
            ),
        ],
        responses={
            200: BorrowerListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single borrower (if id provided) or a paginated list of borrowers.",
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
                include_deleted = (
                    request.query_params.get("include_deleted", "false").lower()
                    == "true"
                )
                borrower = BorrowerService.get_by_id(id, include_deleted)
                if not borrower:
                    return _error(
                        data={"detail": "Borrower not found."},
                        message="Borrower not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = BorrowerReadSerializer(
                    borrower, context={"request": request}
                )

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
                "search": request.query_params.get("search"),
                "name": request.query_params.get("name"),
                "email": request.query_params.get("email"),
                "contact": request.query_params.get("contact"),
                "include_deleted": request.query_params.get(
                    "include_deleted", "false"
                ).lower()
                == "true",
            }
            # Remove None values
            filters = filter_cleaner(filters)

            page = int(request.query_params.get("page", 1))
            limit = int(request.query_params.get("page_size", 20))
            sort_by = request.query_params.get("sort_by", "name")
            sort_order = request.query_params.get("sort_order", "asc")

            result = BorrowerService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )

            paginator = self.pagination_class()
            # logger.debug(f"result: {result}")
            data = BorrowerListSerializer(
                result["data"], many=True, context={"request": request}
            ).data

            # logger.debug(f"Borrower data: {data}")
            response = paginator.get_paginated_response(
                data=data,
                message="Borrowers retrieved successfully.",
                pagination=result["pagination"],
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Borrower",
                object_id="list",
                changes={"count": result["pagination"]["total"]},
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
        description="Create a new borrower. Admin/Staff only.",
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
                data=serializer.validated_data, user=user, request=request
            )

            read_serializer = BorrowerReadSerializer(
                borrower, context={"request": request}
            )

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
        description="Full update of an existing borrower. Admin/Staff only.",
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
            borrower, data=request.data, context={"request": request}
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
                request=request,
            )

            read_serializer = BorrowerReadSerializer(
                updated, context={"request": request}
            )

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
        description="Partial update of an existing borrower. Admin/Staff only.",
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
            borrower, data=request.data, partial=True, context={"request": request}
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
                request=request,
            )

            read_serializer = BorrowerReadSerializer(
                updated, context={"request": request}
            )

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
        description="Soft delete a borrower. Admin only.",
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
            BorrowerService.delete(borrower_id=id, user=user, request=request)

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


# ===================================================================
# RESTORE VIEW
# ===================================================================


class BorrowerRestoreView(APIView):
    """
    Restore a soft-deleted borrower. Admin only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        responses={
            200: BorrowerDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Restore a soft-deleted borrower. Admin only.",
    )
    @transaction.atomic
    def post(self, request, id):
        """Restore a soft-deleted borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Permission check: admin only
        if not is_admin(user):
            return _error(
                data={"detail": "You do not have permission to restore borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            borrower = BorrowerService.restore(
                borrower_id=id, user=user, request=request
            )

            serializer = BorrowerReadSerializer(borrower, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="restore",
                model_name="Borrower",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=serializer.data,
                message="Borrower restored successfully.",
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
            logger.exception("Borrower restore failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to restore borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# PERMANENT DELETE VIEW
# ===================================================================


class BorrowerPermanentDeleteView(APIView):
    """
    Permanently delete a borrower (hard delete). Admin only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        responses={
            204: BorrowerDeleteResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Permanently delete a borrower (hard delete). Admin only.",
    )
    @transaction.atomic
    def delete(self, request, id):
        """Permanently delete a borrower."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Permission check: admin only
        if not is_admin(user):
            return _error(
                data={
                    "detail": "You do not have permission to permanently delete borrowers."
                },
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            BorrowerService.permanent_delete(borrower_id=id, user=user, request=request)

            log_audit_event(
                request=request,
                user=user,
                action_type="permanent_delete",
                model_name="Borrower",
                object_id=str(id),
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=None,
                message="Borrower permanently deleted successfully.",
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
            logger.exception("Borrower permanent delete failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to permanently delete borrower.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# BULK CREATE VIEW
# ===================================================================


class BorrowerBulkCreateView(APIView):
    """
    Bulk create multiple borrowers. Admin/Staff only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        request=inline_serializer(
            name="BulkCreateRequest",
            fields={
                "borrowersArray": serializers.ListField(
                    child=BorrowerCreateSerializer()
                ),
            },
        ),
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": BorrowerReadSerializer(many=True),
                    "errors": serializers.ListField(child=serializers.DictField()),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk create multiple borrowers. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        """Bulk create multiple borrowers."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        borrowers_data = request.data.get("borrowersArray")
        if not isinstance(borrowers_data, list):
            return _error(
                data={"detail": "borrowersArray must be a list."},
                message="Invalid request format.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = BorrowerService.bulk_create(
                borrowers_data, user=user, request=request
            )

            # Serialize created borrowers
            created_serialized = BorrowerReadSerializer(
                result["created"], many=True, context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_create",
                model_name="Borrower",
                object_id="bulk",
                changes={
                    "count": len(result["created"]),
                    "errors": len(result["errors"]),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"created": created_serialized, "errors": result["errors"]},
                message="Bulk create completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk create failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk create borrowers.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# BULK UPDATE VIEW
# ===================================================================


class BorrowerBulkUpdateView(APIView):
    """
    Bulk update multiple borrowers. Admin/Staff only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        request=inline_serializer(
            name="BulkUpdateRequest",
            fields={
                "updatesArray": serializers.ListField(child=serializers.DictField()),
            },
        ),
        responses={
            200: inline_serializer(
                name="BulkUpdateResponse",
                fields={
                    "updated": BorrowerReadSerializer(many=True),
                    "errors": serializers.ListField(child=serializers.DictField()),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Bulk update multiple borrowers. Admin/Staff only.",
    )
    @transaction.atomic
    def put(self, request):
        """Bulk update multiple borrowers."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update borrowers."},
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
            result = BorrowerService.bulk_update(updates, user=user, request=request)

            updated_serialized = BorrowerReadSerializer(
                result["updated"], many=True, context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="bulk_update",
                model_name="Borrower",
                object_id="bulk",
                changes={
                    "count": len(result["updated"]),
                    "errors": len(result["errors"]),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"updated": updated_serialized, "errors": result["errors"]},
                message="Bulk update completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Bulk update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to bulk update borrowers.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# IMPORT VIEW (CSV)
# ===================================================================


class BorrowerImportView(APIView):
    """
    Import borrowers from CSV content. Admin/Staff only.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        request=inline_serializer(
            name="ImportRequest",
            fields={
                "fileContent": serializers.CharField(),
                "fileName": serializers.CharField(required=False),
            },
        ),
        responses={
            201: inline_serializer(
                name="ImportResponse",
                fields={
                    "imported": BorrowerReadSerializer(many=True),
                    "errors": serializers.ListField(child=serializers.DictField()),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Import borrowers from CSV content. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        """Import borrowers from CSV."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to import borrowers."},
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
            borrowers_data = list(reader)
        except Exception as e:
            return _error(
                data={"detail": f"Invalid CSV: {str(e)}"},
                message="CSV parsing error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = BorrowerService.bulk_create(
                borrowers_data, user=user, request=request
            )

            imported_serialized = BorrowerReadSerializer(
                result["created"], many=True, context={"request": request}
            ).data

            log_audit_event(
                request=request,
                user=user,
                action_type="import_csv",
                model_name="Borrower",
                object_id="import",
                changes={
                    "count": len(result["created"]),
                    "errors": len(result["errors"]),
                },
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"imported": imported_serialized, "errors": result["errors"]},
                message="CSV import completed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("CSV import failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to import borrowers.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# EXPORT VIEW
# ===================================================================


class BorrowerExportView(APIView):
    """
    Export borrowers to CSV or JSON.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        request=inline_serializer(
            name="ExportRequest",
            fields={
                "format": serializers.ChoiceField(
                    choices=["csv", "json"], default="json"
                ),
                "filters": serializers.DictField(required=False),
            },
        ),
        responses={
            200: inline_serializer(
                name="ExportResponse",
                fields={
                    "format": serializers.CharField(),
                    "data": serializers.CharField(help_text="CSV string or JSON array"),
                    "filename": serializers.CharField(),
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Export borrowers to CSV or JSON.",
    )
    def post(self, request):
        """Export borrowers."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to export borrowers."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.data.get("format", "json")
        filters = request.data.get("filters", {})

        try:
            exported_data = BorrowerService.export_borrowers(filters)

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
                filename = (
                    f"borrowers_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
                )
            else:  # json
                import json

                data_str = json.dumps(exported_data, default=str)
                filename = (
                    f"borrowers_export_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
                )

            log_audit_event(
                request=request,
                user=user,
                action_type="export",
                model_name="Borrower",
                object_id="export",
                changes={"format": fmt, "count": len(exported_data)},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data={"format": fmt, "data": data_str, "filename": filename},
                message="Export completed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Export failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to export borrowers.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ===================================================================
# STATISTICS VIEW
# ===================================================================


class BorrowerStatisticsView(APIView):
    """
    Get borrower statistics.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Borrowers"],
        responses={
            200: inline_serializer(
                name="BorrowerStatistics",
                fields={
                    "total": serializers.IntegerField(),
                    "with_email": serializers.IntegerField(),
                    "with_contact": serializers.IntegerField(),
                    "recently_added": serializers.IntegerField(),
                    "with_active_debts": serializers.IntegerField(),
                    "total_outstanding_debt": serializers.FloatField(),
                    "deleted": serializers.IntegerField(),
                    "active": serializers.IntegerField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get borrower statistics including totals and counts.",
    )
    def get(self, request):
        """Get borrower statistics."""
        user = request.user

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            stats = BorrowerService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Borrower",
                object_id="statistics",
                changes=stats,
            )

            return _success(
                data=stats,
                message="Statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Statistics fetch failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to fetch statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
