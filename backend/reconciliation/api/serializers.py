"""Response shapes from ARCHITECTURE §5. Amounts serialise as decimal strings (DRF default, D-21)."""
from rest_framework import serializers

from reconciliation.models import BankTransaction, CapitalCall, LineItem, Match, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    """Tenant-neutral switcher data only (D-19): no financial fields."""

    class Meta:
        model = Organization
        fields = ["slug", "name"]


class MatchLineItemSerializer(serializers.ModelSerializer):
    investor_name = serializers.CharField(source="investor_entity.name")

    class Meta:
        model = LineItem
        fields = ["id", "expected_reference", "investor_name"]


class MatchSerializer(serializers.ModelSerializer):
    line_item = MatchLineItemSerializer()

    class Meta:
        model = Match
        fields = ["id", "status", "reason", "line_item"]


class TransactionSerializer(serializers.ModelSerializer):
    """Expects `matches` prefetched in id order (see api.views.transactions_for)."""

    matches = MatchSerializer(many=True, read_only=True)

    class Meta:
        model = BankTransaction
        fields = ["id", "date", "description", "amount", "type", "bank_reference", "status", "review_reason", "matches"]


class CapitalCallSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = CapitalCall
        fields = ["number", "call_date", "due_date"]


class LineItemSerializer(serializers.ModelSerializer):
    capital_call = CapitalCallSummarySerializer(read_only=True)
    investor_name = serializers.CharField(source="investor_entity.name", read_only=True)
    amount_remaining = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = LineItem
        fields = [
            "id", "capital_call", "investor_name", "expected_reference",
            "amount_called", "amount_funded", "amount_remaining", "status",
        ]
