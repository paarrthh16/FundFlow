"""Approve / reject / ignore through the API (PRD §7–§8, AC-21, AC-22, AC-25 to AC-27, AC-29)."""
from decimal import Decimal
from unittest import mock

from rest_framework.test import APITestCase

from reconciliation.constants import LineItemStatus, MatchStatus, TransactionStatus, TransactionType
from reconciliation.models import BankTransaction, LineItem, Match, Organization
from reconciliation.services.actions import ALL_REJECTED_REASON, approve_match
from reconciliation.services.seed import seed_demo

from .helpers import make_match, make_transaction


class ActionTestCase(APITestCase):
    def setUp(self):
        seed_demo()
        self.northwind = Organization.objects.get(slug="northwind")
        self.bluebird = Organization.objects.get(slug="bluebird")
        self.item = {li.expected_reference: li for li in LineItem.objects.all()}

    def credit(self, reference, amount, *line_items, org=None, txn_date="2026-04-15"):
        """A credit with one proposed match per line item (suggested if one, needs_review otherwise)."""
        org = org or self.northwind
        status = TransactionStatus.SUGGESTED if len(line_items) == 1 else TransactionStatus.NEEDS_REVIEW
        txn = make_transaction(org, reference=reference, amount=amount, status=status,
                               review_reason="" if len(line_items) == 1 else "Several candidates")
        BankTransaction.objects.filter(pk=txn.pk).update(date=txn_date)
        matches = [make_match(txn, self.item[ref]) for ref in line_items]
        return txn, matches

    def post(self, path, org="northwind"):
        headers = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.post(path, **headers)

    def approve(self, match, org="northwind"):
        return self.post(f"/api/matches/{match.pk}/approve/", org)

    def reject(self, match, org="northwind"):
        return self.post(f"/api/matches/{match.pk}/reject/", org)

    def ignore(self, txn, org="northwind"):
        return self.post(f"/api/transactions/{txn.pk}/ignore/", org)

    def funded(self, reference):
        item = LineItem.objects.get(pk=self.item[reference].pk)
        return item.amount_funded, item.status

    def state(self, obj):
        obj.refresh_from_db()
        return obj.status


class ApproveTests(ActionTestCase):
    def test_approve_updates_funding_and_reconciles(self):
        txn, (match,) = self.credit("R1", "25000.00", "CC-1002")

        response = self.approve(match)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["id"], body["status"], body["amount"]), (txn.pk, "reconciled", "25000.00"))
        self.assertEqual([(m["id"], m["status"]) for m in body["matches"]], [(match.pk, "approved")])
        self.assertEqual(body["matches"][0]["line_item"]["expected_reference"], "CC-1002")
        self.assertEqual(self.funded("CC-1002"), (Decimal("25000.00"), LineItemStatus.FUNDED))

    def test_partial_then_remaining_payment_sets_funded(self):
        _, (first,) = self.credit("R1", "20000.00", "CC-1001")
        _, (second,) = self.credit("R2", "7500.00", "CC-1001")

        self.assertEqual(self.approve(first).status_code, 200)
        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))
        self.assertEqual(self.approve(second).status_code, 200)
        self.assertEqual(self.funded("CC-1001"), (Decimal("27500.00"), LineItemStatus.FUNDED))

    def test_approve_rejects_sibling_matches(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        self.assertEqual(self.approve(b).status_code, 200)

        self.assertEqual((self.state(a), self.state(b), self.state(txn)),
                         (MatchStatus.REJECTED, MatchStatus.APPROVED, TransactionStatus.RECONCILED))
        self.assertEqual(self.funded("CC-1005"), (Decimal("0.00"), LineItemStatus.OPEN))
        self.assertEqual(self.funded("CC-1006"), (Decimal("15000.00"), LineItemStatus.FUNDED))

    def test_approve_rechecks_remaining_returns_409(self):
        # Two 20,000.00 suggestions for CC-1001 (27,500.00): only one can be funded.
        first_txn, (first,) = self.credit("R1", "20000.00", "CC-1001")
        second_txn, (second,) = self.credit("R2", "20000.00", "CC-1001")
        self.assertEqual(self.approve(first).status_code, 200)

        response = self.approve(second)

        self.assertEqual(response.status_code, 409)
        self.assertIn("exceeds the remaining 7,500.00", response.json()["detail"])
        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))
        self.assertEqual((self.state(second), self.state(second_txn)), (MatchStatus.PROPOSED, TransactionStatus.SUGGESTED))

    def test_overfunding_multi_candidate_match_returns_409_without_changes(self):
        # Several-reference proposals are not overpayment-filtered at import (D-13); approval catches it.
        txn, (big, small) = self.credit("R1", "20000.00", "CC-1001", "CC-1003")

        response = self.approve(small)

        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.state(big), self.state(small), self.state(txn)),
                         (MatchStatus.PROPOSED, MatchStatus.PROPOSED, TransactionStatus.NEEDS_REVIEW))
        self.assertEqual(self.funded("CC-1003"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_second_approval_returns_409_no_double_funding(self):
        _, (match,) = self.credit("R1", "20000.00", "CC-1001")
        self.assertEqual(self.approve(match).status_code, 200)

        response = self.approve(match)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))

    def test_approving_sibling_after_reconcile_returns_409(self):
        _, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")
        self.approve(a)

        self.assertEqual(self.approve(b).status_code, 409)
        self.assertEqual(self.funded("CC-1006"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_approve_rejected_match_returns_409(self):
        _, (match,) = self.credit("R1", "100.00", "CC-1001")
        self.reject(match)

        response = self.approve(match)

        self.assertEqual(response.status_code, 409)
        self.assertIn("is rejected", response.json()["detail"])
        self.assertEqual(self.funded("CC-1001"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_approve_match_on_ignored_transaction_returns_409(self):
        txn, (match,) = self.credit("R1", "100.00", "CC-1001")
        self.ignore(txn)

        self.assertEqual(self.approve(match).status_code, 409)
        self.assertEqual(self.funded("CC-1001"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_approve_rechecks_call_date_eligibility(self):
        # CC-1006 is called on 2026-04-01; a stale proposal for an earlier payment must not fund it.
        _, (match,) = self.credit("R1", "100.00", "CC-1006", txn_date="2026-03-20")
        self.assertEqual(self.approve(match).status_code, 409)
        self.assertEqual(self.funded("CC-1006"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_failure_after_funding_rolls_back_everything(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        with mock.patch.object(BankTransaction, "save", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                approve_match(self.northwind, a.pk)

        self.assertEqual(self.funded("CC-1005"), (Decimal("0.00"), LineItemStatus.OPEN))
        self.assertEqual((self.state(a), self.state(b), self.state(txn)),
                         (MatchStatus.PROPOSED, MatchStatus.PROPOSED, TransactionStatus.NEEDS_REVIEW))


class RejectTests(ActionTestCase):
    def test_reject_last_proposed_match_sets_needs_review(self):
        txn, (match,) = self.credit("R1", "100.00", "CC-1001")

        response = self.reject(match)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "needs_review")
        self.assertEqual(response.json()["review_reason"], ALL_REJECTED_REASON)
        self.assertEqual(self.state(match), MatchStatus.REJECTED)

    def test_reject_one_of_several_keeps_needs_review(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        self.assertEqual(self.reject(a).status_code, 200)
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.review_reason), (TransactionStatus.NEEDS_REVIEW, "Several candidates"))
        self.assertEqual(self.state(b), MatchStatus.PROPOSED)

        self.assertEqual(self.reject(b).status_code, 200)
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.review_reason), (TransactionStatus.NEEDS_REVIEW, ALL_REJECTED_REASON))

    def test_reject_non_proposed_match_returns_409(self):
        _, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")
        self.approve(a)

        self.assertEqual(self.reject(a).status_code, 409)  # approved
        self.assertEqual(self.reject(b).status_code, 409)  # rejected by the sibling approval
        self.assertEqual(self.state(a), MatchStatus.APPROVED)

    def test_reject_twice_returns_409(self):
        _, (match,) = self.credit("R1", "100.00", "CC-1001")
        self.assertEqual(self.reject(match).status_code, 200)
        self.assertEqual(self.reject(match).status_code, 409)

    def test_reject_never_changes_funding(self):
        _, (paid,) = self.credit("R1", "20000.00", "CC-1001")
        self.approve(paid)
        _, (match,) = self.credit("R2", "7500.00", "CC-1001")

        self.reject(match)

        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))


class IgnoreTests(ActionTestCase):
    def test_ignore_rejects_matches(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        response = self.ignore(txn)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        self.assertEqual((self.state(a), self.state(b)), (MatchStatus.REJECTED, MatchStatus.REJECTED))

    def test_ignore_suggested_and_unmatched_needs_review(self):
        suggested, _ = self.credit("R1", "100.00", "CC-1001")
        unmatched = make_transaction(self.northwind, reference="R2", status=TransactionStatus.NEEDS_REVIEW,
                                     review_reason="No eligible line item matched")
        self.assertEqual(self.ignore(suggested).status_code, 200)
        self.assertEqual(self.ignore(unmatched).status_code, 200)
        self.assertEqual(self.state(unmatched), TransactionStatus.IGNORED)

    def test_ignore_reconciled_returns_409(self):
        txn, (match,) = self.credit("R1", "20000.00", "CC-1001")
        self.approve(match)

        response = self.ignore(txn)

        self.assertEqual(response.status_code, 409)
        self.assertIn("reconciled", response.json()["detail"])
        self.assertEqual((self.state(txn), self.state(match)), (TransactionStatus.RECONCILED, MatchStatus.APPROVED))
        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))

    def test_ignore_already_ignored_returns_409(self):
        txn, _ = self.credit("R1", "100.00", "CC-1001")
        self.ignore(txn)
        self.assertEqual(self.ignore(txn).status_code, 409)

    def test_ignore_debit_returns_409(self):
        debit = make_transaction(self.northwind, reference="FEE1", amount="-25.00",
                                 type=TransactionType.DEBIT, status=TransactionStatus.IGNORED)
        self.assertEqual(self.ignore(debit).status_code, 409)

    def test_ignore_never_changes_funding(self):
        _, (paid,) = self.credit("R1", "20000.00", "CC-1001")
        self.approve(paid)
        txn, _ = self.credit("R2", "7500.00", "CC-1001")

        self.ignore(txn)

        self.assertEqual(self.funded("CC-1001"), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))
        self.assertEqual(self.state(paid), MatchStatus.APPROVED)


class OrganizationScopingTests(ActionTestCase):
    def test_missing_and_blank_org_header_returns_400(self):
        txn, (match,) = self.credit("R1", "100.00", "CC-1001")
        for org in (None, "", "   "):
            with self.subTest(org=org):
                self.assertEqual(self.approve(match, org=org).status_code, 400)
                self.assertEqual(self.reject(match, org=org).status_code, 400)
                self.assertEqual(self.ignore(txn, org=org).status_code, 400)
        self.assertEqual(self.state(match), MatchStatus.PROPOSED)

    def test_unknown_org_returns_404(self):
        _, (match,) = self.credit("R1", "100.00", "CC-1001")
        self.assertEqual(self.approve(match, org="unknown").status_code, 404)

    def test_approve_other_org_match_returns_404(self):
        _, (match,) = self.credit("R1", "20000.00", "CC-1001")

        response = self.approve(match, org="bluebird")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state(match), MatchStatus.PROPOSED)
        self.assertEqual(self.funded("CC-1001"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_reject_and_ignore_other_org_record_returns_404(self):
        txn, (match,) = self.credit("R1", "100.00", "CC-2001", org=self.bluebird)

        self.assertEqual(self.reject(match, org="northwind").status_code, 404)
        self.assertEqual(self.ignore(txn, org="northwind").status_code, 404)
        self.assertEqual((self.state(match), self.state(txn)), (MatchStatus.PROPOSED, TransactionStatus.SUGGESTED))

    def test_unknown_ids_return_404(self):
        self.assertEqual(self.post("/api/matches/999999/approve/").status_code, 404)
        self.assertEqual(self.post("/api/matches/999999/reject/").status_code, 404)
        self.assertEqual(self.post("/api/transactions/999999/ignore/").status_code, 404)

    def test_cross_org_match_written_by_bypass_cannot_fund(self):
        # Defence in depth: a northwind match pointing at bluebird's line item (impossible via services).
        txn = make_transaction(self.northwind, reference="R1", amount="100.00")
        bad = Match.objects.create(organization=self.northwind, transaction=txn,
                                   line_item=self.item["CC-2001"], reason="corrupt")

        self.assertEqual(self.approve(bad).status_code, 409)
        self.assertEqual(self.funded("CC-2001"), (Decimal("0.00"), LineItemStatus.OPEN))


class MalformedIdTests(ActionTestCase):
    """D-26: malformed or out-of-range IDs are 404s, returned as JSON like every other API error."""

    PATHS = ("/api/matches/{}/approve/", "/api/matches/{}/reject/", "/api/transactions/{}/ignore/")
    MALFORMED = ("abc", "1.5", "-1", "1e3", "%D9%A3", "99999999999999999999999")  # %D9%A3 = Arabic-Indic "3"

    def test_malformed_ids_return_json_404(self):
        for template in self.PATHS:
            for raw in self.MALFORMED:
                with self.subTest(path=template.format(raw)):
                    response = self.post(template.format(raw))
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response["Content-Type"], "application/json")
                    self.assertIn("not found", response.json()["detail"])

    def test_largest_valid_id_is_a_normal_not_found(self):
        response = self.post(f"/api/matches/{2**63 - 1}/approve/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Match not found."})

    def test_malformed_id_does_not_change_anything(self):
        txn, (match,) = self.credit("R1", "20000.00", "CC-1001")
        self.post("/api/matches/abc/approve/")
        self.assertEqual((self.state(match), self.state(txn)), (MatchStatus.PROPOSED, TransactionStatus.SUGGESTED))
        self.assertEqual(self.funded("CC-1001"), (Decimal("0.00"), LineItemStatus.OPEN))

    def test_malformed_id_still_requires_org_header(self):
        self.assertEqual(self.post("/api/matches/abc/approve/", org=None).status_code, 400)


class ActionResponseShapeTests(ActionTestCase):
    """Pins the exact action-response JSON so serializer refactors cannot change it silently."""

    def test_action_response_shape_is_unchanged(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        body = self.approve(b).json()

        self.assertEqual(list(body), ["id", "date", "description", "amount", "type", "bank_reference",
                                      "status", "review_reason", "matches"])
        self.assertEqual(body, {
            "id": txn.pk, "date": "2026-04-15", "description": "WIRE INCOMING", "amount": "15000.00",
            "type": "credit", "bank_reference": "R1", "status": "reconciled", "review_reason": "Several candidates",
            "matches": [
                {"id": a.pk, "status": "rejected", "reason": "Reference found",
                 "line_item": {"id": self.item["CC-1005"].pk, "expected_reference": "CC-1005",
                               "investor_name": "Oakridge Holdings LLC"}},
                {"id": b.pk, "status": "approved", "reason": "Reference found",
                 "line_item": {"id": self.item["CC-1006"].pk, "expected_reference": "CC-1006",
                               "investor_name": "Oakridge Holdings LLC"}},
            ],
        })
        # Reject and ignore return the same shape.
        _, (proposed,) = self.credit("R2", "100.00", "CC-1001")
        rejected = self.reject(proposed)
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(list(rejected.json()), list(body))
        ignored = self.ignore(make_transaction(self.northwind, reference="R3"))
        self.assertEqual(ignored.status_code, 200)
        self.assertEqual(list(ignored.json()), list(body))
