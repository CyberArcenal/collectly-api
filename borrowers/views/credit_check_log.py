import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from audit.utils.log import log_audit_event
from borrowers.models.credit_check_log import CreditCheckLog
from borrowers.serializers.credit_check_log import (
    CreditCheckLogReadSerializer,
    CreditCheckLogListSerializer,
    CreditCheckLogCreateSerializer,
    CreditCheckLogUpdateSerializer,
)
from django.db.models import Avg, Count
from borrowers.services.credit_check import CreditCheckService
from borrowers.services.borrower import BorrowerService
from users.permissions.base import IsAccountActive, can_read, can_edit
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


class CreditCheckLogListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    pagination = BasePaginatedSerializer()
    data = CreditCheckLogListSerializer(many=True)


class CreditCheckLogDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = CreditCheckLogReadSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------


class CreditCheckLogCRUDView(APIView):
    """
    CRUD operations for credit check logs.
    """

    permission_classes = [IsAuthenticated, IsAccountActive]
    pagination_class = CustomPagination

    # ------------------------------------------------------------------
    # GET /credit-checks/  (list) and GET /credit-checks/<id>/ (retrieve)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Credit Checks"],
        parameters=[
            OpenApiParameter(
                name="page", type=int, description="Page number", required=False
            ),
            OpenApiParameter(
                name="page_size", type=int, description="Items per page", required=False
            ),
            OpenApiParameter(
                name="debtor_id",
                type=int,
                description="Filter by debtor ID",
                required=False,
            ),
            OpenApiParameter(
                name="risk_level",
                type=str,
                description="Filter by risk level",
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
        ],
        responses={
            200: CreditCheckLogListResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Retrieve a single credit check log (if id provided) or a paginated list.",
    )
    def get(self, request, id=None):
        """Retrieve single credit check log or list all."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view credit checks."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if id:
                log_entry = CreditCheckService.get_by_id(id)
                if not log_entry:
                    return _error(
                        data={"detail": "Credit check log not found."},
                        message="Credit check log not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                serializer = CreditCheckLogReadSerializer(
                    log_entry, context={"request": request}
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="CreditCheckLog",
                    object_id=str(id),
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return _success(
                    data=serializer.data,
                    message="Credit check log retrieved successfully.",
                    status=status.HTTP_200_OK,
                )

            # List with filters
            debtor_id = request.query_params.get("debtor_id")
            if debtor_id:
                # Validate debtor exists
                debtor = BorrowerService.get_by_id(debtor_id)
                if not debtor:
                    return _error(
                        data={"detail": "Debtor not found."},
                        message="Debtor not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )

                page = int(request.query_params.get("page", 1))
                limit = int(request.query_params.get("page_size", 20))

                result = CreditCheckService.get_by_borrower(
                    borrower_id=debtor_id, page=page, limit=limit
                )

                serialized_data = CreditCheckLogListSerializer(
                    result["data"], many=True, context={"request": request}
                ).data

                paginator = self.pagination_class()
                response = paginator.get_paginated_response(
                    data=serialized_data,
                    message="Credit check logs retrieved successfully.",
                    pagination=result["pagination"],
                )

                log_audit_event(
                    request=request,
                    user=user,
                    action_type="read",
                    model_name="CreditCheckLog",
                    object_id="list",
                    changes={"debtor_id": debtor_id},
                    ip_address=client_ip,
                    user_agent=user_agent,
                )

                return response

            # Get latest credit check for a debtor
            latest_debtor_id = request.query_params.get("latest_for_debtor")
            if latest_debtor_id:
                latest = CreditCheckService.get_latest(latest_debtor_id)
                if not latest:
                    return _success(
                        data=None,
                        message="No credit check found for this debtor.",
                        status=status.HTTP_200_OK,
                    )

                serializer = CreditCheckLogReadSerializer(
                    latest, context={"request": request}
                )
                return _success(
                    data=serializer.data,
                    message="Latest credit check retrieved.",
                    status=status.HTTP_200_OK,
                )

            return _error(
                data={
                    "detail": "debtor_id or latest_for_debtor parameter is required for listing."
                },
                message="Missing required parameter.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as exc:
            logger.exception("Credit check retrieval error")
            return _error(
                data={"detail": str(exc)},
                message="An error occurred.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # POST /credit-checks/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Credit Checks"],
        request=CreditCheckLogCreateSerializer,
        responses={
            201: CreditCheckLogDetailResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Create a new credit check log. Admin/Staff only.",
    )
    @transaction.atomic
    def post(self, request):
        user = request.user
        client_ip = get_client_ip(request)

        if not can_edit(user):
            return _error(
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data.copy()
        data["performed_by"] = user.pk

        serializer = CreditCheckLogCreateSerializer(data=data)

        if not serializer.is_valid():
            return _error(
                data=serializer.errors,
                message="Validation error.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log_entry = serializer.save()

            # Return the computed score
            read_serializer = CreditCheckLogReadSerializer(
                log_entry, context={"request": request}
            )

            return _success(
                data=read_serializer.data,
                message="Credit check performed successfully.",
                status=status.HTTP_201_CREATED,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Credit check creation failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to perform credit check.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ------------------------------------------------------------------
    # PUT /credit-checks/<id>/
    # NOT ALLOWED - CreditCheckLogs are immutable (only soft delete allowed)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Credit Checks"],
        request=CreditCheckLogUpdateSerializer,
        responses={
            405: inline_serializer(
                name="MethodNotAllowedResponse",
                fields={
                    "status": serializers.BooleanField(default=False),
                    "message": serializers.CharField(),
                },
            ),
        },
        description="Credit check logs are immutable. PUT is not allowed.",
        exclude=True,
    )
    def put(self, request, id=None):
        return _error(
            data={"detail": "Credit check logs are immutable."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # PATCH /credit-checks/<id>/
    # NOT ALLOWED - CreditCheckLogs are immutable (only soft delete allowed)
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Credit Checks"],
        request=CreditCheckLogUpdateSerializer,
        responses={
            405: inline_serializer(
                name="MethodNotAllowedResponse",
                fields={
                    "status": serializers.BooleanField(default=False),
                    "message": serializers.CharField(),
                },
            ),
        },
        description="Credit check logs are immutable. PATCH is not allowed.",
        exclude=True,
    )
    def patch(self, request, id=None):
        return _error(
            data={"detail": "Credit check logs are immutable."},
            message="Method not allowed.",
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ------------------------------------------------------------------
    # DELETE /credit-checks/<id>/
    # WITH TRANSACTION
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Credit Checks"],
        responses={
            204: inline_serializer(
                name="DeleteSuccessResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Soft delete a credit check log. Admin/Staff only.",
    )
    @transaction.atomic
    def delete(self, request, id):
        """Soft delete a credit check log."""
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_edit(user):
            return _error(
                data={"detail": "You do not have permission to delete credit checks."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        log_entry = CreditCheckService.get_by_id(id)
        if not log_entry:
            return _error(
                data={"detail": "Credit check log not found."},
                message="Credit check log not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            CreditCheckService.delete(log_id=id, user=user, request=request)

            return _success(
                data=None,
                message="Credit check log deleted successfully.",
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as exc:
            transaction.set_rollback(True)
            logger.exception("Credit check deletion failed")
            return _error(
                data={"detail": str(exc)},
                message="Failed to delete credit check log.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Credit Check Statistics View (UPDATED)
# ----------------------------------------------------------------------


class CreditCheckStatsView(APIView):
    """
    Get credit check statistics.

    Returns statistics matching the Electron offline implementation:
    - total_checks: Total number of credit checks
    - average_score: Average credit score
    - risk_level_distribution: Counts per risk level (Low, Medium, High)
    - last_check_date: Date of the most recent credit check (ISO format)
    """

    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Credit Checks"],
        parameters=[
            OpenApiParameter(
                name="debtor_id",
                type=int,
                description="Filter by debtor ID (optional)",
                required=False,
            ),
            OpenApiParameter(
                name="start_date",
                type=str,
                description="Filter checks from this date (YYYY-MM-DD)",
                required=False,
            ),
            OpenApiParameter(
                name="end_date",
                type=str,
                description="Filter checks up to this date (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: inline_serializer(
                name="CreditCheckStatsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="CreditCheckStatsData",
                        fields={
                            "total_checks": serializers.IntegerField(),
                            "average_score": serializers.FloatField(),
                            "risk_level_distribution": serializers.DictField(
                                child=serializers.IntegerField()
                            ),
                            "last_check_date": serializers.CharField(allow_null=True),
                            "excellent_count": serializers.IntegerField(),
                            "good_count": serializers.IntegerField(),
                            "fair_count": serializers.IntegerField(),
                            "poor_count": serializers.IntegerField(),
                            "passing_count": serializers.IntegerField(),
                            "score_range": {
                                "min": serializers.IntegerField(),
                                "max": serializers.IntegerField(),
                            },
                            "risk_distribution": serializers.ListField(),
                        },
                    ),
                },
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get credit check statistics. Filters are optional.",
    )
    def get(self, request):
        try:
            # Get optional filters
            debtor_id = request.query_params.get("debtor_id")
            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")

            # If debtor_id is provided, filter by that debtor
            # Also supports date filters for more granular stats
            qs = CreditCheckLog.objects.filter(deleted_at__isnull=True)

            if debtor_id:
                debtor = BorrowerService.get_by_id(debtor_id)
                if not debtor:
                    return _error(
                        data={"detail": "Debtor not found."},
                        message="Debtor not found.",
                        status=status.HTTP_404_NOT_FOUND,
                    )
                qs = qs.filter(debtor_id=debtor_id)

            if start_date:
                qs = qs.filter(date_checked__gte=start_date)

            if end_date:
                qs = qs.filter(date_checked__lte=end_date)

            # Compute statistics (using the updated service method)
            stats = CreditCheckService.get_statistics(debtor_id)

            # If date filters are applied and we have a specific debtor,
            # we need to recalculate because the service method doesn't accept date filters
            # Actually, the service method already handles the filtered qs
            # but we need to ensure the stats are computed from the filtered queryset

            # For simplicity, we'll use the filtered queryset directly
            total_checks = qs.count()
            if total_checks > 0:
                avg_score = qs.aggregate(avg=Avg("score"))["avg"] or 0

                # Risk distribution from filtered queryset
                risk_dist_raw = qs.values("risk_level").annotate(count=Count("id"))
                risk_level_distribution = {"Low": 0, "Medium": 0, "High": 0}
                for item in risk_dist_raw:
                    level = item["risk_level"]
                    if level in risk_level_distribution:
                        risk_level_distribution[level] = item["count"]

                latest = qs.order_by("-date_checked").first()
                last_check_date = latest.date_checked.isoformat() if latest else None

                stats = {
                    "total_checks": total_checks,
                    "average_score": round(float(avg_score), 2),
                    "risk_level_distribution": risk_level_distribution,
                    "last_check_date": last_check_date,
                }
            else:
                stats = {
                    "total_checks": 0,
                    "average_score": 0,
                    "risk_level_distribution": {"Low": 0, "Medium": 0, "High": 0},
                    "last_check_date": None,
                }

            return _success(
                data=stats,
                message="Credit check statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Credit check stats error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve credit check statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
