from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from reconciliation.constants import LineItemStatus, MatchStatus, TransactionStatus
from reconciliation.models import BankTransaction, CapitalCall, InvestorEntity, LineItem, Match, Organization

from .helpers import make_match, make_transaction

EXPECTED_LINE_ITEMS = {
    # reference: (org, call number, investor, amount called)
    "CC-1001": ("northwind", 1, "Haller Capital LLC", Decimal("27500.00")),
    "CC-1002": ("northwind", 1, "Rahul Shah Trust", Decimal("25000.00")),
    "CC-1003": ("northwind", 1, "Patel Family Trust", Decimal("12500.00")),
    "CC-1004": ("northwind", 1, "Meridian Partners LP", Decimal("15000.00")),
    "CC-1005": ("northwind", 1, "Oakridge Holdings LLC", Decimal("15000.00")),
    "CC-1006": ("northwind", 2, "Oakridge Holdings LLC", Decimal("15000.00")),
    "CC-2001": ("bluebird", 1, "Cedar Grove Fund LP", Decimal("40000.00")),
}


def run_seed():
    out = StringIO()
    call_command("seed_demo", stdout=out)
    return out.getvalue()


def counts():
    return {
        model.__name__: model.objects.count()
        for model in (Organization, CapitalCall, InvestorEntity, LineItem, BankTransaction, Match)
    }


class SeedDemoTests(TestCase):
    def test_seed_demo_creates_expected_data(self):
        run_seed()

        self.assertEqual(
            dict(Organization.objects.values_list("slug", "name")),
            {"northwind": "Northwind Ventures", "bluebird": "Bluebird Capital"},
        )
        self.assertEqual(
            set(CapitalCall.objects.values_list("organization__slug", "number", "call_date", "due_date")),
            {
                ("northwind", 1, date(2026, 3, 25), date(2026, 4, 10)),
                ("northwind", 2, date(2026, 4, 1), date(2026, 4, 20)),
                ("bluebird", 1, date(2026, 4, 1), date(2026, 4, 15)),
            },
        )
        self.assertEqual(InvestorEntity.objects.count(), 6)  # Oakridge has line items on two calls

        actual = {
            item.expected_reference: (
                item.organization.slug, item.capital_call.number, item.investor_entity.name, item.amount_called
            )
            for item in LineItem.objects.select_related("organization", "capital_call", "investor_entity")
        }
        self.assertEqual(actual, EXPECTED_LINE_ITEMS)
        for item in LineItem.objects.all():
            self.assertEqual(item.amount_funded, Decimal("0.00"))
            self.assertEqual(item.status, LineItemStatus.OPEN)
            self.assertEqual(item.organization_id, item.capital_call.organization_id)
            self.assertEqual(item.organization_id, item.investor_entity.organization_id)

    def test_seed_demo_twice_no_duplicates(self):
        first_output = run_seed()
        first = counts()
        second_output = run_seed()

        self.assertEqual(counts(), first)
        self.assertEqual(first["LineItem"], 7)
        # The report counts records, not lookups (Oakridge is one entity with two line items).
        self.assertIn("investor entities: 6 created, 0 already present", first_output)
        self.assertIn("investor entities: 0 created, 6 already present", second_output)
        self.assertIn("line items: 0 created, 7 already present", second_output)
        self.assertNotIn("differs from seed value", second_output)

    def test_seed_demo_preserves_existing_funding_and_history(self):
        run_seed()
        haller = LineItem.objects.get(expected_reference="CC-1001")
        haller.amount_funded = Decimal("20000.00")
        haller.recompute_status()
        haller.save()
        oakridge = LineItem.objects.get(expected_reference="CC-1006")
        txn = make_transaction(haller.organization, reference="ACH20260404B", amount="20000.00",
                               status=TransactionStatus.RECONCILED)
        approved = make_match(txn, haller, status=MatchStatus.APPROVED)
        rejected = make_match(txn, oakridge, status=MatchStatus.REJECTED)
        before = counts()

        run_seed()

        haller.refresh_from_db()
        self.assertEqual(haller.amount_funded, Decimal("20000.00"))
        self.assertEqual(haller.status, LineItemStatus.PARTIALLY_FUNDED)
        txn.refresh_from_db()
        self.assertEqual(txn.status, TransactionStatus.RECONCILED)
        self.assertEqual(Match.objects.get(pk=approved.pk).status, MatchStatus.APPROVED)
        self.assertEqual(Match.objects.get(pk=rejected.pk).status, MatchStatus.REJECTED)
        self.assertEqual(counts(), before)

    def test_seed_demo_warns_but_never_overwrites_changed_values(self):
        run_seed()
        Organization.objects.filter(slug="bluebird").update(name="Bluebird Renamed")

        output = run_seed()

        self.assertEqual(Organization.objects.get(slug="bluebird").name, "Bluebird Renamed")
        self.assertIn("differs from seed value", output)

    def test_seed_demo_recreates_only_missing_records(self):
        run_seed()
        LineItem.objects.filter(expected_reference="CC-2001").delete()

        output = run_seed()

        self.assertEqual(LineItem.objects.count(), 7)
        self.assertIn("line items: 1 created, 6 already present", output)
