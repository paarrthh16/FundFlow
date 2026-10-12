"""
Database-level safeguards (PRD §13.1). Each write bypasses services and model validation on purpose:
the point is that PostgreSQL itself rejects the bad row.
"""
from decimal import Decimal

from django.db import DataError, IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from reconciliation.constants import LineItemStatus, MatchStatus, TransactionStatus, TransactionType
from reconciliation.models import CapitalCall, InvestorEntity, LineItem, Organization

from .helpers import make_line_item, make_match, make_org, make_transaction


class DatabaseConstraintTestCase(TestCase):
    def assertRejected(self, func, error=IntegrityError):
        with self.assertRaises(error), transaction.atomic():
            func()


class LineItemConstraintTests(DatabaseConstraintTestCase):
    def setUp(self):
        self.org = make_org()
        self.item = make_line_item(self.org, called="1000.00")

    def update(self, **fields):
        LineItem.objects.filter(pk=self.item.pk).update(**fields)

    def test_db_rejects_funded_above_called(self):
        self.assertRejected(lambda: self.update(amount_funded=Decimal("1000.01"), status=LineItemStatus.PARTIALLY_FUNDED))
        self.assertRejected(lambda: self.update(amount_funded=Decimal("1000.01"), status=LineItemStatus.FUNDED))

    def test_db_rejects_negative_funding(self):
        self.assertRejected(lambda: self.update(amount_funded=Decimal("-1.00")))

    def test_db_rejects_non_positive_amount_called(self):
        self.assertRejected(lambda: self.update(amount_called=Decimal("0.00")))

    def test_db_rejects_status_that_disagrees_with_amounts(self):
        self.assertRejected(lambda: self.update(status=LineItemStatus.FUNDED))
        self.assertRejected(lambda: self.update(amount_funded=Decimal("500.00")))  # still "open"
        self.assertRejected(lambda: self.update(status="paid"))

    def test_db_accepts_consistent_funding_states(self):
        self.update(amount_funded=Decimal("500.00"), status=LineItemStatus.PARTIALLY_FUNDED)
        self.update(amount_funded=Decimal("1000.00"), status=LineItemStatus.FUNDED)

    def test_db_rejects_amount_beyond_storage_precision(self):
        self.assertRejected(lambda: self.update(amount_called=Decimal("1000000000000.00")), error=DataError)

    def test_db_rejects_second_line_item_for_same_call_and_investor(self):
        self.assertRejected(lambda: LineItem.objects.create(
            organization=self.org, capital_call=self.item.capital_call, investor_entity=self.item.investor_entity,
            expected_reference="CC-OTHER", amount_called=Decimal("5.00"),
        ))

    def test_expected_reference_is_not_unique(self):
        # D-28: a shared reference is allowed; matching then sees several candidates.
        make_line_item(self.org, investor="Another LP", reference=self.item.expected_reference)
        self.assertEqual(LineItem.objects.filter(expected_reference=self.item.expected_reference).count(), 2)


class BankTransactionConstraintTests(DatabaseConstraintTestCase):
    def setUp(self):
        self.org = make_org()

    def test_db_rejects_duplicate_bank_reference_in_org(self):
        make_transaction(self.org, reference="FT20260403A")
        self.assertRejected(lambda: make_transaction(self.org, reference="FT20260403A"))

    def test_same_bank_reference_allowed_in_other_org(self):
        make_transaction(self.org, reference="FT20260403A")
        make_transaction(make_org("other", "Other"), reference="FT20260403A")

    def test_db_rejects_amount_sign_that_disagrees_with_type(self):
        self.assertRejected(lambda: make_transaction(self.org, amount="-25.00"))
        self.assertRejected(lambda: make_transaction(
            self.org, amount="25.00", type=TransactionType.DEBIT, status=TransactionStatus.IGNORED))
        self.assertRejected(lambda: make_transaction(self.org, amount="0.00"))
        self.assertRejected(lambda: make_transaction(self.org, type="refund"))

    def test_db_rejects_debit_not_ignored(self):
        self.assertRejected(lambda: make_transaction(
            self.org, amount="-25.00", type=TransactionType.DEBIT, status=TransactionStatus.SUGGESTED))
        make_transaction(self.org, amount="-25.00", type=TransactionType.DEBIT, status=TransactionStatus.IGNORED)

    def test_db_rejects_needs_review_without_reason(self):
        self.assertRejected(lambda: make_transaction(self.org, status=TransactionStatus.NEEDS_REVIEW))
        make_transaction(self.org, status=TransactionStatus.NEEDS_REVIEW, review_reason="No eligible line item")

    def test_db_rejects_invalid_status_and_empty_required_fields(self):
        self.assertRejected(lambda: make_transaction(self.org, status="pending"))
        self.assertRejected(lambda: make_transaction(self.org, reference=""))
        self.assertRejected(lambda: make_transaction(self.org, description=""))


class MatchConstraintTests(DatabaseConstraintTestCase):
    def setUp(self):
        self.org = make_org()
        self.item_a = make_line_item(self.org, investor="A", reference="CC-1")
        self.item_b = make_line_item(self.org, investor="B", reference="CC-2")
        self.txn = make_transaction(self.org, status=TransactionStatus.NEEDS_REVIEW, review_reason="Two candidates")

    def test_db_rejects_second_approved_match(self):
        make_match(self.txn, self.item_a, status=MatchStatus.APPROVED)
        self.assertRejected(lambda: make_match(self.txn, self.item_b, status=MatchStatus.APPROVED))

    def test_several_proposed_and_rejected_matches_are_allowed(self):
        make_match(self.txn, self.item_a, status=MatchStatus.APPROVED)
        make_match(self.txn, self.item_b, status=MatchStatus.REJECTED)
        other_txn = make_transaction(self.org, reference="FT0002")
        make_match(other_txn, self.item_a)
        make_match(other_txn, self.item_b)

    def test_approved_matches_on_different_transactions_are_allowed(self):
        make_match(self.txn, self.item_a, status=MatchStatus.APPROVED)
        make_match(make_transaction(self.org, reference="FT0002"), self.item_a, status=MatchStatus.APPROVED)

    def test_db_rejects_duplicate_match_for_same_line_item(self):
        make_match(self.txn, self.item_a)
        self.assertRejected(lambda: make_match(self.txn, self.item_a, status=MatchStatus.REJECTED))

    def test_db_rejects_empty_reason_and_invalid_status(self):
        self.assertRejected(lambda: make_match(self.txn, self.item_a, reason=""))
        self.assertRejected(lambda: make_match(self.txn, self.item_a, status="maybe"))


class ReferenceDataConstraintTests(DatabaseConstraintTestCase):
    def setUp(self):
        self.org = make_org()

    def test_unique_natural_keys(self):
        self.assertRejected(lambda: Organization.objects.create(slug="acme", name="Duplicate"))
        InvestorEntity.objects.create(organization=self.org, name="Haller Capital LLC")
        self.assertRejected(lambda: InvestorEntity.objects.create(organization=self.org, name="Haller Capital LLC"))
        make_line_item(self.org, number=1)
        self.assertRejected(lambda: CapitalCall.objects.create(
            organization=self.org, number=1, call_date="2026-01-01", due_date="2026-01-31"))

    def test_financial_records_are_protected_from_cascade_delete(self):
        item = make_line_item(self.org)
        make_match(make_transaction(self.org), item)
        with self.assertRaises(ProtectedError):
            self.org.delete()
        with self.assertRaises(ProtectedError):
            item.delete()
