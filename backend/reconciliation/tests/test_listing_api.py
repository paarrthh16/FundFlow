"""Read-only listing endpoints (ARCHITECTURE §5, PRD §9–§10, AC-29, AC-29b, AC-30, D-19, D-20)."""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from reconciliation.constants import TransactionStatus
from reconciliation.models import BankTransaction, LineItem, Organization
from reconciliation.services.seed import seed_demo

from .helpers import make_match, make_transaction


class ListingTestCase(APITestCase):
    def setUp(self):
        seed_demo()
        self.northwind = Organization.objects.get(slug="northwind")
        self.bluebird = Organization.objects.get(slug="bluebird")
        self.item = {li.expected_reference: li for li in LineItem.objects.all()}

    def get(self, path, org="northwind"):
        headers = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.get(path, **headers)

    def transaction(self, org, reference, txn_date, *line_items):
        txn = make_transaction(org, reference=reference)
        BankTransaction.objects.filter(pk=txn.pk).update(date=txn_date)
        for ref in line_items:
            make_match(txn, self.item[ref], reason=f"Reference {ref} found")
        return txn


class OrganizationListTests(ListingTestCase):
    def test_organization_list_needs_no_header_and_exposes_no_financial_data(self):
        response = self.client.get("/api/organizations/")  # no X-Organization header

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {"slug": "bluebird", "name": "Bluebird Capital"},
            {"slug": "northwind", "name": "Northwind Ventures"},
        ])

    def test_organization_list_ignores_org_header(self):
        self.assertEqual(len(self.get("/api/organizations/", org="unknown").json()), 2)

    def test_admin_route_not_exposed(self):
        self.assertEqual(self.client.get("/admin/").status_code, 404)


class TransactionListTests(ListingTestCase):
    def test_transactions_listed_newest_first(self):
        old = self.transaction(self.northwind, "R-OLD", date(2026, 4, 1))
        same_day_first = self.transaction(self.northwind, "R-SAME-1", date(2026, 4, 10))
        same_day_second = self.transaction(self.northwind, "R-SAME-2", date(2026, 4, 10))
        newest = self.transaction(self.northwind, "R-NEW", date(2026, 4, 20))

        ids = [t["id"] for t in self.get("/api/transactions/").json()]

        # date DESC, then id DESC for equal dates (D-20)
        self.assertEqual(ids, [newest.pk, same_day_second.pk, same_day_first.pk, old.pk])

    def test_nested_matches_and_reasons_preserved(self):
        txn = self.transaction(self.northwind, "R1", date(2026, 4, 11), "CC-1006", "CC-1005")
        BankTransaction.objects.filter(pk=txn.pk).update(
            status=TransactionStatus.NEEDS_REVIEW, review_reason="Name and amount match 2 line items")

        (body,) = self.get("/api/transactions/").json()

        self.assertEqual(body["review_reason"], "Name and amount match 2 line items")
        self.assertEqual(
            [(m["line_item"]["expected_reference"], m["status"], m["reason"]) for m in body["matches"]],
            [("CC-1006", "proposed", "Reference CC-1006 found"), ("CC-1005", "proposed", "Reference CC-1005 found")],
        )
        self.assertEqual(body["matches"][0]["line_item"]["investor_name"], "Oakridge Holdings LLC")
        self.assertEqual(body["amount"], "1000.00")

    def test_empty_list_is_a_plain_array(self):
        self.assertEqual(self.get("/api/transactions/").json(), [])

    def test_transaction_list_query_count_does_not_grow(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.get("/api/transactions/")
            self.assertEqual(response.status_code, 200)
            return len(response.json()), len(ctx.captured_queries)

        for i in range(3):
            self.transaction(self.northwind, f"A{i}", date(2026, 4, 15), "CC-1001", "CC-1002")
        small = count_queries()
        for i in range(20):
            self.transaction(self.northwind, f"B{i}", date(2026, 4, 15), "CC-1003", "CC-1004", "CC-1005")
        large = count_queries()

        self.assertEqual((small[0], large[0]), (3, 23))
        # organization lookup + transactions + prefetched matches (with line item and investor joined)
        self.assertEqual(small[1], 3)
        self.assertEqual(large[1], 3)


class LineItemListTests(ListingTestCase):
    def test_line_item_list_fields(self):
        body = self.get("/api/line-items/").json()

        self.assertEqual(body[0], {
            "id": self.item["CC-1001"].pk,
            "capital_call": {"number": 1, "call_date": "2026-03-25", "due_date": "2026-04-10"},
            "investor_name": "Haller Capital LLC",
            "expected_reference": "CC-1001",
            "amount_called": "27500.00",
            "amount_funded": "0.00",
            "amount_remaining": "27500.00",
            "status": "open",
        })

    def test_line_items_ordered_by_call_date_then_reference(self):
        refs = [li["expected_reference"] for li in self.get("/api/line-items/").json()]
        self.assertEqual(refs, ["CC-1001", "CC-1002", "CC-1003", "CC-1004", "CC-1005", "CC-1006"])

    def test_remaining_after_approval(self):
        txn = make_transaction(self.northwind, reference="R1", amount="20000.00")
        match = make_match(txn, self.item["CC-1001"])
        approved = self.client.post(f"/api/matches/{match.pk}/approve/", HTTP_X_ORGANIZATION="northwind")
        self.assertEqual(approved.status_code, 200)

        haller = next(li for li in self.get("/api/line-items/").json() if li["expected_reference"] == "CC-1001")

        self.assertEqual(
            (haller["amount_funded"], haller["amount_remaining"], haller["status"]),
            ("20000.00", "7500.00", "partially_funded"),
        )
        for key in ("amount_called", "amount_funded", "amount_remaining"):
            self.assertIsInstance(haller[key], str)

    def test_line_item_list_query_count(self):
        with self.assertNumQueries(2):  # organization lookup + line items with call and investor joined
            self.get("/api/line-items/")


class ListingTenantTests(ListingTestCase):
    def test_lists_scoped_to_organization(self):
        nw = self.transaction(self.northwind, "SHARED-REF", date(2026, 4, 15), "CC-1001")
        bb = self.transaction(self.bluebird, "SHARED-REF", date(2026, 4, 16), "CC-2001")

        nw_txns = self.get("/api/transactions/").json()
        bb_txns = self.get("/api/transactions/", org="bluebird").json()
        self.assertEqual([t["id"] for t in nw_txns], [nw.pk])
        self.assertEqual([t["id"] for t in bb_txns], [bb.pk])
        self.assertEqual(bb_txns[0]["matches"][0]["line_item"]["expected_reference"], "CC-2001")

        self.assertEqual([li["expected_reference"] for li in self.get("/api/line-items/", org="bluebird").json()],
                         ["CC-2001"])
        self.assertNotIn("CC-2001", [li["expected_reference"] for li in self.get("/api/line-items/").json()])

    def test_listing_header_errors(self):
        for path in ("/api/transactions/", "/api/line-items/"):
            for org, expected in ((None, 400), ("", 400), ("   ", 400), ("unknown", 404), ("Northwind", 404)):
                with self.subTest(path=path, org=org):
                    response = self.get(path, org=org)
                    self.assertEqual(response.status_code, expected)
                    self.assertIn("detail", response.json())

    def test_listing_is_read_only(self):
        self.transaction(self.northwind, "R1", date(2026, 4, 15), "CC-1001")
        for path in ("/api/organizations/", "/api/transactions/", "/api/line-items/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, HTTP_X_ORGANIZATION="northwind").status_code, 405)
        self.assertEqual(BankTransaction.objects.count(), 1)
        self.assertEqual(LineItem.objects.filter(amount_funded__gt=0).count(), 0)
