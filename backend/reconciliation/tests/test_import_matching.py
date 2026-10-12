"""Import-time matching against seeded data (PRD §15.1, AC-14 to AC-20)."""
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from rest_framework.test import APITestCase

from reconciliation.constants import LineItemStatus, MatchStatus, TransactionStatus
from reconciliation.models import BankTransaction, LineItem, Match, Organization
from reconciliation.services.importer import _build_matches, import_statement
from reconciliation.services.matching import Candidate, Proposal
from reconciliation.services.seed import seed_demo

from .helpers import make_line_item

URL = "/api/imports/"
HEADER = "date,description,amount,type,reference"
SAMPLE_CSV = Path(__file__).resolve().parent / "data" / "sample_bank_statement.csv"

# PRD §15.1: bank reference -> (status, proposed references) for northwind.
NORTHWIND_EXPECTED = {
    "FT20260403A": (TransactionStatus.SUGGESTED, ["CC-1002"]),
    "ACH20260404B": (TransactionStatus.SUGGESTED, ["CC-1001"]),
    "FEE20260405": (TransactionStatus.IGNORED, []),
    "FT20260407C": (TransactionStatus.SUGGESTED, ["CC-1003"]),
    "FT20260408E": (TransactionStatus.SUGGESTED, ["CC-1004"]),
    "FT20260409D": (TransactionStatus.NEEDS_REVIEW, []),
    "FT20260411G": (TransactionStatus.NEEDS_REVIEW, ["CC-1005", "CC-1006"]),
    "ACH20260414H": (TransactionStatus.SUGGESTED, ["CC-1001"]),
    "FT20260415J": (TransactionStatus.NEEDS_REVIEW, []),
    "FT20260416K": (TransactionStatus.NEEDS_REVIEW, []),
    "FT20260320N": (TransactionStatus.NEEDS_REVIEW, []),
}


def sample_file():
    return SimpleUploadedFile("sample.csv", SAMPLE_CSV.read_bytes())


def csv_file(*lines):
    return SimpleUploadedFile("statement.csv", ("\n".join(lines) + "\n").encode("utf-8"))


def outcomes(org_slug):
    result = {}
    for txn in BankTransaction.objects.filter(organization__slug=org_slug).prefetch_related("matches__line_item"):
        refs = sorted(m.line_item.expected_reference for m in txn.matches.all())
        result[txn.bank_reference] = (txn.status, refs)
    return result


class ImportMatchingTestCase(APITestCase):
    def setUp(self):
        seed_demo()

    def upload(self, file, org="northwind"):
        return self.client.post(URL, {"file": file}, format="multipart", HTTP_X_ORGANIZATION=org)


class SampleCsvTests(ImportMatchingTestCase):
    def test_sample_csv_end_to_end_outcomes(self):
        response = self.upload(sample_file())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported_count"], 11)
        self.assertEqual(outcomes("northwind"), NORTHWIND_EXPECTED)
        self.assertTrue(all(m.status == MatchStatus.PROPOSED for m in Match.objects.all()))

    def test_sample_csv_reasons(self):
        self.upload(sample_file())
        reasons = {
            m.transaction.bank_reference: m.reason
            for m in Match.objects.select_related("transaction").filter(line_item__expected_reference__in=["CC-1002", "CC-1003"])
        }
        self.assertEqual(reasons["FT20260403A"], "Reference CC-1002 found; amount equals remaining 25,000.00")
        self.assertEqual(reasons["FT20260407C"], "Name PATEL FAMILY TRUST found; amount equals remaining 12,500.00")
        review = dict(BankTransaction.objects.filter(status=TransactionStatus.NEEDS_REVIEW)
                      .values_list("bank_reference", "review_reason"))
        self.assertIn("overpayment", review["FT20260415J"])
        self.assertEqual(review["FT20260411G"], "Name and amount match 2 line items (CC-1005, CC-1006); choose one")

    def test_every_match_and_needs_review_has_reason(self):
        self.upload(sample_file())
        self.upload(sample_file(), org="bluebird")
        self.assertFalse(Match.objects.filter(reason="").exists())
        self.assertFalse(BankTransaction.objects.filter(status=TransactionStatus.NEEDS_REVIEW, review_reason="").exists())
        self.assertFalse(BankTransaction.objects.filter(status=TransactionStatus.SUGGESTED, matches=None).exists())

    def test_bluebird_sample_outcomes(self):
        self.upload(sample_file(), org="bluebird")
        result = outcomes("bluebird")

        self.assertEqual(result["FT20260416K"], (TransactionStatus.SUGGESTED, ["CC-2001"]))
        self.assertEqual(result["FEE20260405"], (TransactionStatus.IGNORED, []))
        others = {ref: value for ref, value in result.items() if ref not in {"FT20260416K", "FEE20260405"}}
        self.assertEqual(len(others), 9)
        self.assertEqual(list(others.values()), [(TransactionStatus.NEEDS_REVIEW, [])] * 9)

    def test_suggestions_do_not_change_funding(self):
        self.upload(sample_file())
        self.assertEqual(
            set(LineItem.objects.values_list("amount_funded", "status")),
            {(Decimal("0.00"), LineItemStatus.OPEN)},
        )

    def test_reupload_creates_no_transactions_or_matches(self):
        self.upload(sample_file())
        before = (BankTransaction.objects.count(), Match.objects.count())

        body = self.upload(sample_file()).json()

        self.assertEqual((body["imported_count"], body["duplicate_count"]), (0, 12))
        self.assertEqual([e["line"] for e in body["errors"]], [13, 14])
        self.assertEqual((BankTransaction.objects.count(), Match.objects.count()), before)


class EligibilityIntegrationTests(ImportMatchingTestCase):
    def test_debit_stored_ignored_with_no_matches(self):
        # A debit quoting a valid reference is still never matched.
        self.upload(csv_file(HEADER, "2026-04-05,REFUND CC-1002,-25000.00,debit,D1"))
        txn = BankTransaction.objects.get()
        self.assertEqual((txn.status, txn.matches.count()), (TransactionStatus.IGNORED, 0))

    def test_funded_line_item_is_not_a_candidate(self):
        patel = LineItem.objects.get(expected_reference="CC-1003")
        patel.amount_funded = patel.amount_called
        patel.recompute_status()
        patel.save()

        self.upload(csv_file(HEADER, "2026-04-15,WIRE INCOMING PATEL FAMILY TRUST CC-1003,12500.00,credit,R1"))

        txn = BankTransaction.objects.get()
        self.assertEqual((txn.status, txn.matches.count()), (TransactionStatus.NEEDS_REVIEW, 0))

    def test_partially_funded_line_item_uses_remaining(self):
        haller = LineItem.objects.get(expected_reference="CC-1001")
        haller.amount_funded = Decimal("20000.00")
        haller.recompute_status()
        haller.save()

        self.upload(csv_file(HEADER, "2026-04-14,ACH CREDIT HALLER CAPITAL,7500.00,credit,R1"))

        self.assertEqual(outcomes("northwind")["R1"], (TransactionStatus.SUGGESTED, ["CC-1001"]))
        haller.refresh_from_db()
        self.assertEqual(haller.amount_funded, Decimal("20000.00"))

    def test_two_credits_in_one_file_can_both_suggest_same_line_item(self):
        # Proposals reserve nothing (PRD §6.2); approval re-checks later.
        self.upload(csv_file(HEADER, "2026-04-04,A CC-1001,20000.00,credit,R1", "2026-04-05,B CC-1001,20000.00,credit,R2"))
        result = outcomes("northwind")
        self.assertEqual(result["R1"], (TransactionStatus.SUGGESTED, ["CC-1001"]))
        self.assertEqual(result["R2"], (TransactionStatus.SUGGESTED, ["CC-1001"]))


class TenantIsolationTests(ImportMatchingTestCase):
    def test_cross_org_line_items_never_candidates(self):
        # bluebird's CC-2001 is referenced and its name and amount match exactly, but the upload is northwind's.
        self.upload(csv_file(HEADER, "2026-04-16,WIRE INCOMING CEDAR GROVE FUND CC-2001,40000.00,credit,R1"))

        txn = BankTransaction.objects.get()
        self.assertEqual((txn.status, txn.matches.count()), (TransactionStatus.NEEDS_REVIEW, 0))

    def test_build_matches_refuses_line_item_from_other_org(self):
        # Defence in depth: even if a foreign candidate slipped through, it is rejected before bulk_create.
        northwind = Organization.objects.get(slug="northwind")
        txn = BankTransaction.objects.create(
            organization=northwind, date="2026-04-16", description="X", amount=Decimal("1.00"),
            type="credit", bank_reference="R1", status=TransactionStatus.SUGGESTED,
        )
        foreign = Candidate.from_line_item(LineItem.objects.get(expected_reference="CC-2001"))

        with self.assertRaises(ValidationError):
            _build_matches(northwind, [(txn, Proposal(foreign, "Reference CC-2001 found"))])

    def test_matches_belong_to_the_importing_organization(self):
        acme = Organization.objects.create(slug="acme", name="Acme")
        make_line_item(acme, investor="Rahul Shah Trust", reference="CC-1002", called="25000.00")

        self.upload(sample_file())

        for match in Match.objects.select_related("transaction", "line_item"):
            self.assertEqual(match.organization.slug, "northwind")
            self.assertEqual(match.transaction.organization_id, match.organization_id)
            self.assertEqual(match.line_item.organization_id, match.organization_id)


class ImportAtomicityTests(ImportMatchingTestCase):
    def test_match_insert_failure_rolls_back_imported_transactions(self):
        northwind = Organization.objects.get(slug="northwind")
        seen_before_failure = {}

        def fail_after_transactions_inserted(*args, **kwargs):
            # Proves the transactions really were written inside the import before the failure.
            seen_before_failure["transactions"] = BankTransaction.objects.filter(organization=northwind).count()
            raise IntegrityError("simulated match insert failure")

        with mock.patch.object(Match.objects, "bulk_create", side_effect=fail_after_transactions_inserted):
            with self.assertRaisesMessage(IntegrityError, "simulated match insert failure"):
                import_statement(northwind, SAMPLE_CSV.read_bytes())

        self.assertEqual(seen_before_failure["transactions"], 11)
        self.assertEqual(BankTransaction.objects.count(), 0)
        self.assertEqual(Match.objects.count(), 0)
