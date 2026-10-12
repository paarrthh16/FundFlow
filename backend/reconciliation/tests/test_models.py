from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from reconciliation.constants import LineItemStatus, MatchStatus
from reconciliation.models import LineItem, Match

from .helpers import make_line_item, make_match, make_org, make_transaction


class LineItemModelTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.item = make_line_item(self.org, called="27500.00")

    def test_new_line_item_is_open_with_zero_funding(self):
        self.assertEqual(self.item.amount_funded, Decimal("0.00"))
        self.assertEqual(self.item.status, LineItemStatus.OPEN)
        self.assertEqual(self.item.amount_remaining, Decimal("27500.00"))

    def test_amounts_are_decimals_after_reload(self):
        item = LineItem.objects.get(pk=self.item.pk)
        self.assertIsInstance(item.amount_called, Decimal)
        self.assertIsInstance(item.amount_funded, Decimal)

    def test_recompute_status_follows_funding(self):
        for funded, expected in [
            ("0.00", LineItemStatus.OPEN),
            ("20000.00", LineItemStatus.PARTIALLY_FUNDED),
            ("27500.00", LineItemStatus.FUNDED),
        ]:
            self.item.amount_funded = Decimal(funded)
            self.item.recompute_status()
            self.item.save()  # would violate the status CHECK if recompute_status were wrong
            self.assertEqual(self.item.status, expected)

    def test_relationships(self):
        self.assertEqual(self.item.capital_call.organization, self.org)
        self.assertEqual(self.item.investor_entity.organization, self.org)
        self.assertIn(self.item, self.org.line_items.all())
        self.assertIn(self.item, self.item.capital_call.line_items.all())

    def test_full_clean_rejects_more_than_two_decimal_places(self):
        # PostgreSQL numeric(14,2) would silently round 1.005; model validation must catch it first.
        self.item.amount_called = Decimal("1.005")
        with self.assertRaises(ValidationError):
            self.item.full_clean()


class CrossOrganizationValidationTests(TestCase):
    """Ordinary foreign keys cannot enforce same-organization links, so clean() does (PRD SV1)."""

    def setUp(self):
        self.northwind = make_org("northwind", "Northwind Ventures")
        self.bluebird = make_org("bluebird", "Bluebird Capital")
        self.nw_item = make_line_item(self.northwind)

    def test_line_item_rejects_capital_call_from_other_org(self):
        item = LineItem(
            organization=self.bluebird,
            capital_call=self.nw_item.capital_call,
            investor_entity=self.nw_item.investor_entity,
            expected_reference="CC-1",
            amount_called=Decimal("10.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            item.full_clean()
        self.assertIn("capital_call", ctx.exception.message_dict)
        self.assertIn("investor_entity", ctx.exception.message_dict)

    def test_match_rejects_line_item_from_other_org(self):
        txn = make_transaction(self.bluebird)
        match = Match(organization=self.bluebird, transaction=txn, line_item=self.nw_item, reason="x")
        with self.assertRaises(ValidationError) as ctx:
            match.full_clean()
        self.assertEqual(list(ctx.exception.message_dict), ["line_item"])

    def test_match_within_one_org_is_valid(self):
        txn = make_transaction(self.northwind)
        Match(organization=self.northwind, transaction=txn, line_item=self.nw_item, reason="x").full_clean()


class MatchRelationshipTests(TestCase):
    def test_transaction_lists_its_matches(self):
        org = make_org()
        a = make_line_item(org, investor="A", reference="CC-1")
        b = make_line_item(org, investor="B", reference="CC-2")
        txn = make_transaction(org)
        make_match(txn, a)
        make_match(txn, b)

        self.assertEqual(txn.matches.count(), 2)
        self.assertEqual(set(txn.matches.values_list("status", flat=True)), {MatchStatus.PROPOSED})
