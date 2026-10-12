"""Pure matching rules (PRD §6.2–6.5). Candidates are plain objects; no database."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from reconciliation.constants import TransactionStatus
from reconciliation.services.matching import NO_MATCH_REASON, Candidate, match_credit

TXN_DATE = date(2026, 4, 15)


def candidate(reference, name, remaining, call_date=date(2026, 3, 25)):
    return Candidate(line_item=reference, reference=reference, investor_name=name,
                     call_date=call_date, remaining=Decimal(remaining))


HALLER = candidate("CC-1001", "Haller Capital LLC", "27500.00")
RAHUL = candidate("CC-1002", "Rahul Shah Trust", "25000.00")
PATEL = candidate("CC-1003", "Patel Family Trust", "12500.00")
OAK_1 = candidate("CC-1005", "Oakridge Holdings LLC", "15000.00")
OAK_2 = candidate("CC-1006", "Oakridge Holdings LLC", "15000.00", call_date=date(2026, 4, 1))
ALL = [HALLER, RAHUL, PATEL, OAK_1, OAK_2]


def match(description, amount, bank_reference="FT0001", candidates=ALL, txn_date=TXN_DATE):
    return match_credit(Decimal(amount), txn_date, description, bank_reference, candidates)


def proposed(outcome):
    return [p.candidate.reference for p in outcome.proposals]


class ReferenceStepTests(SimpleTestCase):
    def test_reference_match_full_payment_suggested(self):
        outcome = match("WIRE INCOMING RAHUL SHAH TRUST REF CC-1002", "25000.00")
        self.assertEqual(outcome.status, TransactionStatus.SUGGESTED)
        self.assertEqual(proposed(outcome), ["CC-1002"])
        self.assertEqual(outcome.proposals[0].reason, "Reference CC-1002 found; amount equals remaining 25,000.00")
        self.assertEqual(outcome.review_reason, "")

    def test_reference_match_lowercase_cc1001(self):
        outcome = match("ACH CREDIT HALLER CAPITAL cc1001", "27500.00")
        self.assertEqual((outcome.status, proposed(outcome)), (TransactionStatus.SUGGESTED, ["CC-1001"]))

    def test_reference_match_partial_payment_suggested(self):
        outcome = match("ACH CREDIT HALLER CAPITAL LLC CC-1001", "20000.00")
        self.assertEqual((outcome.status, proposed(outcome)), (TransactionStatus.SUGGESTED, ["CC-1001"]))
        self.assertEqual(
            outcome.proposals[0].reason,
            "Reference CC-1001 found; amount 20,000.00 is less than remaining 27,500.00 (partial payment)",
        )

    def test_reference_found_in_bank_reference_field(self):
        outcome = match("INCOMING WIRE", "100.00", bank_reference="BANK-CC1003-X")
        self.assertEqual(proposed(outcome), ["CC-1003"])

    def test_reference_overpayment_needs_review_without_match(self):
        outcome = match("WIRE INCOMING PATEL FAMILY TRUST CC-1003", "13000.00")
        self.assertEqual(outcome.status, TransactionStatus.NEEDS_REVIEW)
        self.assertEqual(outcome.proposals, ())
        self.assertEqual(
            outcome.review_reason,
            "Reference CC-1003 found, but amount 13,000.00 exceeds remaining 12,500.00 (overpayment); no match proposed",
        )

    def test_overpayment_does_not_fall_back_to_name_step(self):
        # The name "RAHUL SHAH TRUST" and amount 25,000.00 would match CC-1002 in step 2,
        # but a reference candidate exists, so step 2 is skipped.
        outcome = match("RAHUL SHAH TRUST CC-1003", "25000.00")
        self.assertEqual((outcome.status, outcome.proposals), (TransactionStatus.NEEDS_REVIEW, ()))
        self.assertIn("overpayment", outcome.review_reason)

    def test_multiple_reference_candidates_all_proposed(self):
        # CC-1003 would be overpaid by 20,000.00 but is still proposed (no overpayment filter, D-13).
        outcome = match("PAYMENT CC-1001 AND CC-1003", "20000.00")
        self.assertEqual(outcome.status, TransactionStatus.NEEDS_REVIEW)
        self.assertEqual(proposed(outcome), ["CC-1001", "CC-1003"])
        self.assertEqual([p.reason for p in outcome.proposals], ["Reference CC-1001 found", "Reference CC-1003 found"])
        self.assertEqual(
            outcome.review_reason,
            "References for 2 line items found (CC-1001 Haller Capital LLC, CC-1003 Patel Family Trust); choose one",
        )

    def test_shared_expected_reference_proposes_all(self):
        twin_a = candidate("CC-7000", "Alpha LP", "100.00")
        twin_b = candidate("CC-7000", "Beta LP", "100.00")
        outcome = match("WIRE CC-7000", "100.00", candidates=[twin_a, twin_b])
        self.assertEqual(outcome.status, TransactionStatus.NEEDS_REVIEW)
        self.assertEqual([p.candidate for p in outcome.proposals], [twin_a, twin_b])

    def test_reference_found_skips_name_step(self):
        # Name + exact amount would match PATEL, but CC-1001 is referenced (partial) so it wins.
        outcome = match("PATEL FAMILY TRUST CC-1001", "12500.00")
        self.assertEqual(proposed(outcome), ["CC-1001"])

    def test_reference_substring_containment_is_literal(self):
        # Documented trade-off (PRD §6.1): "CC1001" appears inside "CC10010".
        self.assertEqual(proposed(match("WIRE CC10010", "100.00")), ["CC-1001"])


class NameStepTests(SimpleTestCase):
    def test_name_and_exact_amount_match_suggested(self):
        outcome = match("WIRE INCOMING PATEL FAMILY TRUST", "12500.00")
        self.assertEqual((outcome.status, proposed(outcome)), (TransactionStatus.SUGGESTED, ["CC-1003"]))
        self.assertEqual(outcome.proposals[0].reason, "Name PATEL FAMILY TRUST found; amount equals remaining 12,500.00")

    def test_name_match_multiple_candidates_needs_review(self):
        outcome = match("WIRE INCOMING OAKRIDGE HOLDINGS LLC", "15000.00", txn_date=date(2026, 4, 11))
        self.assertEqual(outcome.status, TransactionStatus.NEEDS_REVIEW)
        self.assertEqual(proposed(outcome), ["CC-1005", "CC-1006"])
        self.assertEqual(outcome.review_reason, "Name and amount match 2 line items (CC-1005, CC-1006); choose one")
        self.assertEqual(
            {p.reason for p in outcome.proposals},
            {"Name OAKRIDGE HOLDINGS found; amount equals remaining 15,000.00"},
        )

    def test_name_match_requires_exact_amount(self):
        for amount in ("12499.99", "12500.01", "6250.00"):
            with self.subTest(amount=amount):
                outcome = match("WIRE INCOMING PATEL FAMILY TRUST", amount)
                self.assertEqual((outcome.status, outcome.proposals), (TransactionStatus.NEEDS_REVIEW, ()))

    def test_name_match_whole_words_only(self):
        outcome = match("WIRE INCOMING PATELFAMILY TRUST", "12500.00")
        self.assertEqual(outcome.proposals, ())

    def test_no_match_needs_review_with_reason(self):
        outcome = match("UNKNOWN INCOMING WIRE", "9999.99")
        self.assertEqual(outcome, outcome.__class__(TransactionStatus.NEEDS_REVIEW, NO_MATCH_REASON, ()))


class EligibilityTests(SimpleTestCase):
    def test_ineligible_future_call_date_excluded(self):
        # On 2026-03-20 neither Oakridge call has been issued (calls dated 03-25 and 04-01).
        outcome = match("WIRE INCOMING OAKRIDGE HOLDINGS LLC REF CC-1006", "15000.00", txn_date=date(2026, 3, 20))
        self.assertEqual((outcome.status, outcome.proposals), (TransactionStatus.NEEDS_REVIEW, ()))
        self.assertEqual(outcome.review_reason, NO_MATCH_REASON)

    def test_call_date_equal_to_transaction_date_is_eligible(self):
        outcome = match("WIRE CC-1006", "15000.00", txn_date=date(2026, 4, 1))
        self.assertEqual(proposed(outcome), ["CC-1006"])

    def test_funded_line_item_excluded_and_falls_through(self):
        funded_patel = candidate("CC-1003", "Patel Family Trust", "0.00")
        patel_two = candidate("CC-3003", "Patel Family Trust", "500.00")
        outcome = match("PATEL FAMILY TRUST CC-1003", "500.00", candidates=[funded_patel, patel_two])
        # CC-1003 is not a candidate, so the name step runs and finds the other Patel item.
        self.assertEqual((outcome.status, proposed(outcome)), (TransactionStatus.SUGGESTED, ["CC-3003"]))

    def test_partially_funded_uses_remaining_amount(self):
        haller_partial = candidate("CC-1001", "Haller Capital LLC", "7500.00")
        by_name = match("ACH CREDIT HALLER CAPITAL", "7500.00", candidates=[haller_partial])
        self.assertEqual((by_name.status, proposed(by_name)), (TransactionStatus.SUGGESTED, ["CC-1001"]))
        overpaid = match("ACH CREDIT HALLER CAPITAL CC-1001", "8000.00", candidates=[haller_partial])
        self.assertEqual((overpaid.status, overpaid.proposals), (TransactionStatus.NEEDS_REVIEW, ()))

    def test_empty_normalized_reference_never_matches(self):
        blank_ref = candidate("--", "Someone", "100.00")
        self.assertEqual(match("WIRE --", "100.00", candidates=[blank_ref]).proposals, ())
