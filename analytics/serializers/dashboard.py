# analytics/serializers/dashboard.py
from rest_framework import serializers


class RevenueDataSerializer(serializers.Serializer):
    """Revenue data serializer."""
    totalRevenue = serializers.FloatField()
    transactionCount = serializers.IntegerField()
    period = serializers.CharField()


class OverviewDataSerializer(serializers.Serializer):
    """Overview data serializer."""
    todayRevenue = serializers.FloatField()
    totalCustomers = serializers.IntegerField()
    activeDebts = serializers.IntegerField()
    overdueDebts = serializers.IntegerField()


class DashboardStatsSerializer(serializers.Serializer):
    """Dashboard statistics serializer."""
    totalBorrowers = serializers.IntegerField()
    totalDebts = serializers.IntegerField()
    totalPaidDebts = serializers.IntegerField()
    totalOverdue = serializers.IntegerField()
    totalPaymentsCollected = serializers.FloatField()
    totalPenaltiesCollected = serializers.FloatField()
    totalRemainingBalance = serializers.FloatField()


class TopProductSerializer(serializers.Serializer):
    """Top product serializer."""
    name = serializers.CharField()
    totalValue = serializers.FloatField()


class LowStockItemSerializer(serializers.Serializer):
    """Low stock / due soon item serializer."""
    id = serializers.IntegerField()
    name = serializers.CharField()
    dueDate = serializers.DateField()


class RecentActivitySerializer(serializers.Serializer):
    """Recent activity serializer."""
    id = serializers.CharField()
    action = serializers.CharField()
    entity = serializers.CharField()
    entityId = serializers.IntegerField(allow_null=True)
    user = serializers.CharField()
    timestamp = serializers.DateTimeField()
    details = serializers.CharField(allow_null=True, required=False)


class SalesTrendPointSerializer(serializers.Serializer):
    """Sales trend point serializer."""
    date = serializers.DateField()
    total = serializers.FloatField()


class PaymentMethodBreakdownSerializer(serializers.Serializer):
    """Payment method breakdown serializer."""
    method = serializers.CharField()
    count = serializers.IntegerField()
    total = serializers.FloatField()