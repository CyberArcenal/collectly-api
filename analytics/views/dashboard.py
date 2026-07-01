# analytics/views/dashboard.py
import logging
from rest_framework.views import APIView
from rest_framework import status, serializers
from rest_framework.permissions import IsAuthenticated

from analytics.serializers.dashboard import (
    OverviewDataSerializer,
    RevenueDataSerializer,
    DashboardStatsSerializer,
    TopProductSerializer,
    LowStockItemSerializer,
    RecentActivitySerializer,
    SalesTrendPointSerializer,
    PaymentMethodBreakdownSerializer,
)
from analytics.services.dashboard import DashboardService
from users.permissions.base import IsAccountActive, can_read
from utils.response import _success, _error
from utils.security import get_client_ip
from audit.utils.log import log_audit_event

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    inline_serializer,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Error Response Serializer
# ----------------------------------------------------------------------

class ErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.DictField(allow_null=True, required=False)


# ----------------------------------------------------------------------
# Dashboard Overview View
# ----------------------------------------------------------------------

class DashboardOverviewView(APIView):
    """
    Get dashboard overview data.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        responses={
            200: inline_serializer(
                name="OverviewResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": OverviewDataSerializer(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get dashboard overview (today's revenue, customer counts, etc.)"
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view dashboard."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            data = DashboardService.get_overview()

            log_audit_event(
                request=request,
                user=user,
                action_type="read_stats",
                model_name="Dashboard",
                object_id="overview",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Dashboard overview retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Dashboard overview error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve dashboard overview.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Revenue View
# ----------------------------------------------------------------------

class DashboardRevenueView(APIView):
    """
    Get revenue data for a given period.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter(
                name="period",
                type=str,
                description="Period: 'today', 'week', 'month', 'year'",
                required=False,
                default="month",
            ),
            OpenApiParameter(
                name="startDate",
                type=str,
                description="Start date (YYYY-MM-DD)",
                required=False,
            ),
            OpenApiParameter(
                name="endDate",
                type=str,
                description="End date (YYYY-MM-DD)",
                required=False,
            ),
        ],
        responses={
            200: inline_serializer(
                name="RevenueResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": RevenueDataSerializer(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get revenue data by period."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view revenue data."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            period = request.query_params.get('period', 'month')
            start_date = request.query_params.get('startDate')
            end_date = request.query_params.get('endDate')

            data = DashboardService.get_revenue(
                period=period,
                start_date=start_date,
                end_date=end_date
            )

            log_audit_event(
                request=request,
                user=user,
                action_type="read_stats",
                model_name="Dashboard",
                object_id="revenue",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Revenue data retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Revenue data error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve revenue data.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Statistics View
# ----------------------------------------------------------------------

class DashboardStatisticsView(APIView):
    """
    Get dashboard statistics.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        responses={
            200: inline_serializer(
                name="StatisticsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": DashboardStatsSerializer(),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get dashboard statistics (borrowers, debts, payments, penalties)."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view statistics."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            data = DashboardService.get_statistics()

            log_audit_event(
                request=request,
                user=user,
                action_type="stats_read",
                model_name="Dashboard",
                object_id="statistics",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Dashboard statistics retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Dashboard statistics error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve dashboard statistics.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Top Products View
# ----------------------------------------------------------------------

class DashboardTopProductsView(APIView):
    """
    Get top products (debts) by total amount.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                description="Number of top products to return",
                required=False,
                default=5,
            ),
        ],
        responses={
            200: inline_serializer(
                name="TopProductsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": TopProductSerializer(many=True),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get top products (debts) by total amount."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view top products."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.query_params.get('limit', 5))
            data = DashboardService.get_top_products(limit=limit)

            log_audit_event(
                request=request,
                user=user,
                action_type="read_stats",
                model_name="Dashboard",
                object_id="top_products",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Top products retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Top products error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve top products.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Low Stock View
# ----------------------------------------------------------------------

class DashboardLowStockView(APIView):
    """
    Get debts due soon (low stock).
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter(
                name="threshold",
                type=int,
                description="Threshold in days",
                required=False,
                default=5,
            ),
        ],
        responses={
            200: inline_serializer(
                name="LowStockResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": LowStockItemSerializer(many=True),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get debts due soon (within threshold days)."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view low stock."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            threshold = int(request.query_params.get('threshold', 5))
            data = DashboardService.get_low_stock(threshold=threshold)

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Dashboard",
                object_id="low_stock",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Low stock items retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Low stock error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve low stock items.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Recent Activities View
# ----------------------------------------------------------------------

class DashboardRecentActivitiesView(APIView):
    """
    Get recent activities.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                description="Number of activities to return",
                required=False,
                default=10,
            ),
        ],
        responses={
            200: inline_serializer(
                name="RecentActivitiesResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": RecentActivitySerializer(many=True),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get recent activities (audit logs and payments)."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view recent activities."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            limit = int(request.query_params.get('limit', 10))
            data = DashboardService.get_recent_activities(limit=limit)

            log_audit_event(
                request=request,
                user=user,
                action_type="read",
                model_name="Dashboard",
                object_id="recent_activities",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Recent activities retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Recent activities error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve recent activities.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Sales Trend View
# ----------------------------------------------------------------------

class DashboardSalesTrendView(APIView):
    """
    Get sales trend over time.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        parameters=[
            OpenApiParameter(
                name="days",
                type=int,
                description="Number of past days to include",
                required=False,
                default=7,
            ),
        ],
        responses={
            200: inline_serializer(
                name="SalesTrendResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": SalesTrendPointSerializer(many=True),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get daily sales trend over a number of days."
    )
    def get(self, request):
        user = request.user
        client_ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        if not can_read(user):
            return _error(
                data={"detail": "You do not have permission to view sales trend."},
                message="Permission denied.",
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            days = int(request.query_params.get('days', 7))
            data = DashboardService.get_sales_trend(days=days)

            log_audit_event(
                request=request,
                user=user,
                action_type="read_stats",
                model_name="Dashboard",
                object_id="sales_trend",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Sales trend retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Sales trend error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve sales trend.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----------------------------------------------------------------------
# Dashboard Payment Methods View
# ----------------------------------------------------------------------

class DashboardPaymentMethodsView(APIView):
    """
    Get payment methods breakdown.
    """
    permission_classes = [IsAuthenticated, IsAccountActive]

    @extend_schema(
        tags=["Dashboard"],
        responses={
            200: inline_serializer(
                name="PaymentMethodsResponse",
                fields={
                    "status": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": PaymentMethodBreakdownSerializer(many=True),
                }
            ),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
        description="Get payment methods breakdown with usage statistics."
    )
    def get(self, request):
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
            data = DashboardService.get_payment_methods_breakdown()

            log_audit_event(
                request=request,
                user=user,
                action_type="read_stats",
                model_name="Dashboard",
                object_id="payment_methods",
                ip_address=client_ip,
                user_agent=user_agent,
            )

            return _success(
                data=data,
                message="Payment methods breakdown retrieved successfully.",
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("Payment methods error")
            return _error(
                data={"detail": str(exc)},
                message="Failed to retrieve payment methods breakdown.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )