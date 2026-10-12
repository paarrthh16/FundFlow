import re

from django.db import DatabaseError, connection
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from reconciliation.exceptions import InvalidFileError, NotFoundError
from reconciliation.models import BankTransaction, LineItem, Match, Organization
from reconciliation.services.actions import approve_match, ignore_transaction, reject_match
from reconciliation.services.importer import import_statement

from .organization import OrganizationScopedAPIView
from .serializers import LineItemSerializer, OrganizationSerializer, TransactionSerializer


class HealthView(APIView):
    """Liveness check. Reports only whether the app and its database respond; never connection details."""

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except DatabaseError:
            return Response(
                {"status": "error", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})


class ImportView(OrganizationScopedAPIView):
    """POST a bank statement CSV as multipart field `file`; returns the import summary."""

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise InvalidFileError("Upload a CSV file in the 'file' form field.")
        summary = import_statement(self.organization, upload.read())
        return Response(summary.as_dict(), status=status.HTTP_200_OK)


MAX_ID = 2**63 - 1  # PostgreSQL bigint primary keys
ID_RE = re.compile(r"[0-9]+")


def parse_id(raw, label):
    """A malformed or out-of-range ID can't exist, so it is a 404 like any unknown ID (D-26)."""
    if not ID_RE.fullmatch(raw) or int(raw) > MAX_ID:
        raise NotFoundError(f"{label} not found.")
    return int(raw)


def transactions_for(organization):
    """One organization's transactions with matches prefetched in id order (no N+1 queries)."""
    matches = Match.objects.select_related("line_item__investor_entity").order_by("pk")
    return BankTransaction.objects.filter(organization=organization).prefetch_related(Prefetch("matches", queryset=matches))


def transaction_response(organization, txn):
    """Action responses re-read the updated transaction through the same queryset as the list."""
    return Response(TransactionSerializer(transactions_for(organization).get(pk=txn.pk)).data)


class ApproveMatchView(OrganizationScopedAPIView):
    def post(self, request, pk):
        return transaction_response(self.organization, approve_match(self.organization, parse_id(pk, "Match")))


class RejectMatchView(OrganizationScopedAPIView):
    def post(self, request, pk):
        return transaction_response(self.organization, reject_match(self.organization, parse_id(pk, "Match")))


class IgnoreTransactionView(OrganizationScopedAPIView):
    def post(self, request, pk):
        txn = ignore_transaction(self.organization, parse_id(pk, "Transaction"))
        return transaction_response(self.organization, txn)


class OrganizationListView(APIView):
    """Switcher options. Tenant-neutral, so no X-Organization header (D-19)."""

    def get(self, request):
        return Response(OrganizationSerializer(Organization.objects.order_by("slug"), many=True).data)


class TransactionListView(OrganizationScopedAPIView):
    def get(self, request):
        transactions = transactions_for(self.organization).order_by("-date", "-id")  # newest first (D-20)
        return Response(TransactionSerializer(transactions, many=True).data)


class LineItemListView(OrganizationScopedAPIView):
    def get(self, request):
        items = (
            LineItem.objects.filter(organization=self.organization)
            .select_related("capital_call", "investor_entity")
            .order_by("capital_call__call_date", "expected_reference", "id")  # D-20
        )
        return Response(LineItemSerializer(items, many=True).data)
