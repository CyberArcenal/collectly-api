import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from loan_agreements.models.loan_agreement import LoanAgreement
from loan_agreements.serializers.loan_agreement import (
    LoanAgreementReadSerializer,
    LoanAgreementListSerializer,
    LoanAgreementCreateSerializer,
    LoanAgreementUpdateSerializer,
    LoanAgreementSignSerializer,
)
from loan_agreements.services.loan_agreement import LoanAgreementService
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

class LoanAgreementListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = LoanAgreementListSerializer(many=True)


class LoanAgreementDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanAgreementReadSerializer()


class LoanAgreementCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanAgreementReadSerializer()


class LoanAgreementUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanAgreementReadSerializer()


class LoanAgreementDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True)


class LoanAgreementSignResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = LoanAgreementReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------

class LoanAgreementCRUDView(APIView):
    """
    CRUD operations for loan agreements.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /loan-agreements/  (list) and GET /loan-agreements/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Agreements"],
        parameters=[
            OpenApiParameter(name="page", type=int, description="Page number", required=False),
            OpenApiParameter(name="page_size", type=int, description="Items per page", required=False),
            OpenApiParameter(name="debt_id", type=int, description="Filter by debt ID", required=False),
            OpenApiParameter(name="status", type=str, description="Filter by status (draft, signed)", required=False),
            OpenApiParameter(name="borrower_id", type=int, description="Filter by borrower ID", required=False),
            OpenApiParameter(name="lender_name", type=str, description="Filter by lender name", required=False),
            OpenApiParameter(name="has_file", type=bool, description="Filter by has file", required=False),
            OpenApiParameter(name="include_deleted", type=bool, description="Include soft-deleted", required=False),
        ],
        responses={
            200: LoanAgreementListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single loan agreement (if id provided) or a paginated list."
    )
    def get(self, request, id=None):
        """Retrieve single loan agreement or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                include_deleted = request.query_params.get('include_deleted', 'false').lower() == 'true'
                agreement = LoanAgreementService.get_by_id(id, include_deleted)
                if not agreement:
                    return _error(
                        data={"detail": "Loan agreement not found."},
                        message="Loan agreement not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = LoanAgreementReadSerializer(agreement, context={"request": request})

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="LoanAgreement",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Loan agreement retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            filters = {
                'debt_id': request.query_params.get('debt_id'),
                'status': request.query_params.get('status'),
                'borrower_id': request.query_params.get('borrower_id'),
                'lender_name': request.query_params.get('lender_name'),
                'has_file': request.query_params.get('has_file'),
                'include_deleted': request.query_params.get('include_deleted', 'false').lower() == 'true',
            }
            # Remove None values
            filters = {k: v for k, v in filters.items() if v is not None}

            # Convert has_file to boolean
            if filters.get('has_file') is not None:
                filters['has_file'] = filters['has_file'].lower() == 'true'

            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('page_size', 20))
            sort_by = request.query_params.get('sort_by', 'created_at')
            sort_order = request.query_params.get('sort_order', 'desc')

            result = LoanAgreementService.get_list(
                filters=filters,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )

            paginator = self.pagination_class()
            response = paginator.get_paginated_response(
                data=result['data'],
                message="Loan agreements retrieved successfully.",
                pagination=result['pagination']
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="LoanAgreement",
                object_id="list",
                changes={"count": result['pagination']['total']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return response

        except Exception as exc:
            logger.exception("Loan agreement retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /loan-agreements/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Agreements"],
        request=LoanAgreementCreateSerializer,
        responses={
            201: LoanAgreementCreateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new loan agreement. Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request):
        """Create a new loan agreement."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to create loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LoanAgreementCreateSerializer(data=request.data)

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="create",
                model_name="LoanAgreement",
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
            agreement = LoanAgreementService.create(
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = LoanAgreementReadSerializer(agreement, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Loan agreement created successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan agreement creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to create loan agreement.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /loan-agreements/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Agreements"],
        request=LoanAgreementUpdateSerializer,
        responses={
            200: LoanAgreementUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Full update of an existing loan agreement. Admin/Staff only. Cannot update signed agreements."
    )
    @transaction.atomic
    def put(self, request, id):
        """Full update of a loan agreement."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        agreement = LoanAgreementService.get_by_id(id)
        if not agreement:
            return _error(
                data={"detail": "Loan agreement not found."},
                message="Loan agreement not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanAgreementUpdateSerializer(
            agreement,
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
            updated = LoanAgreementService.update(
                agreement_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = LoanAgreementReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Loan agreement updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan agreement update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update loan agreement.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PATCH /loan-agreements/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Agreements"],
        request=LoanAgreementUpdateSerializer,
        responses={
            200: LoanAgreementUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Partial update of an existing loan agreement. Admin/Staff only. Cannot update signed agreements."
    )
    @transaction.atomic
    def patch(self, request, id):
        """Partial update of a loan agreement."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to update loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        agreement = LoanAgreementService.get_by_id(id)
        if not agreement:
            return _error(
                data={"detail": "Loan agreement not found."},
                message="Loan agreement not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanAgreementUpdateSerializer(
            agreement,
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
            updated = LoanAgreementService.update(
                agreement_id=id,
                data=serializer.validated_data,
                user=user,
                request=request
            )

            read_serializer = LoanAgreementReadSerializer(updated, context={"request": request})

            return _success(
                data=read_serializer.data,
                message="Loan agreement updated successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan agreement partial update failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to update loan agreement.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # DELETE /loan-agreements/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Loan Agreements"],
        parameters=[
            OpenApiParameter(
                name="allow_delete_signed",
                type=bool,
                description="Allow deletion of signed agreements (admin only)",
                required=False,
                default=False,
            ),
        ],
        responses={
            204: LoanAgreementDeleteResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a loan agreement. Admin/Staff only. Signed agreements can only be deleted by admin with flag."
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a loan agreement."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to delete loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        agreement = LoanAgreementService.get_by_id(id)
        if not agreement:
            return _error(
                data={"detail": "Loan agreement not found."},
                message="Loan agreement not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user can delete signed agreements
        allow_delete_signed = request.query_params.get('allow_delete_signed', 'false').lower() == 'true'
        if agreement.status == LoanAgreement.Status.SIGNED and not allow_delete_signed:
            if not is_admin(user):
                return _error(
                    data={"detail": "Cannot delete signed agreement. Admin override required."},
                    message="Permission denied.",
                    status=status.HTTP_403_FORBIDDEN,
                )

        try:
            LoanAgreementService.delete(
                agreement_id=id,
                user=user,
                request=request,
                allow_delete_signed=allow_delete_signed and is_admin(user)
            )

            return _success(
                data=None,
                message="Loan agreement deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan agreement deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete loan agreement.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Loan Agreement Sign View
# ----------------------------------------------------------------------

class LoanAgreementSignView(APIView):
    """
    Sign a loan agreement (draft → signed).
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Loan Agreements"],
        request=LoanAgreementSignSerializer,
        responses={
            200: LoanAgreementSignResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Sign a loan agreement (draft → signed). Admin/Staff only."
    )
    @transaction.atomic
    def post(self, request, id):
        """Sign a loan agreement."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to sign loan agreements."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        agreement = LoanAgreementService.get_by_id(id)
        if not agreement:
            return _error(
                data={"detail": "Loan agreement not found."},
                message="Loan agreement not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LoanAgreementSignSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.instance = agreement

        if not serializer.is_valid():
            transaction.set_rollback(True)
            log_audit_event(
                request=request,
                user=user,
                action_type="update",
                model_name="LoanAgreement",
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
            signed = serializer.save()

            read_serializer = LoanAgreementReadSerializer(signed, context={"request": request})

            log_audit_event(
                request=request,
                user=user,
                action_type="loan_agreement_signed",
                model_name="LoanAgreement",
                object_id=str(id),
                changes={"signed_by": serializer.validated_data['signed_by']},
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=read_serializer.data,
                message="Loan agreement signed successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Loan agreement signing failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to sign loan agreement.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )