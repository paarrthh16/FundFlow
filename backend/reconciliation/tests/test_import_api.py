from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from reconciliation.constants import TransactionStatus, TransactionType
from reconciliation.models import BankTransaction, Match

from .helpers import make_org, make_transaction

URL = "/api/imports/"
HEADER = "date,description,amount,type,reference"
SAMPLE_CSV = Path(__file__).resolve().parent / "data" / "sample_bank_statement.csv"


def csv_file(*lines, name="statement.csv"):
    return SimpleUploadedFile(name, ("\n".join(lines) + "\n").encode("utf-8"), content_type="text/csv")


class ImportApiTestCase(APITestCase):
    def setUp(self):
        self.northwind = make_org("northwind", "Northwind Ventures")
        self.bluebird = make_org("bluebird", "Bluebird Capital")

    def upload(self, file, org="northwind"):
        headers = {} if org is None else {"HTTP_X_ORGANIZATION": org}
        return self.client.post(URL, {"file": file}, format="multipart", **headers)


class ImportSummaryTests(ImportApiTestCase):
    def test_sample_csv_import_summary(self):
        with open(SAMPLE_CSV, "rb") as f:
            response = self.upload(SimpleUploadedFile("sample.csv", f.read()))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["imported_count"], 11)
        self.assertEqual(body["duplicate_count"], 1)
        self.assertEqual(body["duplicates"], [{
            "line": 11, "reference": "FT20260403A",
            "message": "Duplicate bank reference FT20260403A: same as line 2 in this file",
        }])
        self.assertEqual([e["line"] for e in body["errors"]], [13, 14])
        self.assertIn("Invalid date '2026-04-31'", body["errors"][0]["message"])
        self.assertIn("Credit amount must be positive", body["errors"][1]["message"])
        self.assertEqual(BankTransaction.objects.filter(organization=self.northwind).count(), 11)

    def test_stored_values_are_trimmed_decimal_and_typed(self):
        self.upload(csv_file(HEADER, ' 2026-04-03 , WIRE IN ,"25,000.00", CREDIT , FT1 '))

        txn = BankTransaction.objects.get()
        self.assertEqual(txn.organization, self.northwind)
        self.assertEqual((txn.description, txn.bank_reference, txn.type), ("WIRE IN", "FT1", TransactionType.CREDIT))
        self.assertEqual(txn.amount, Decimal("25000.00"))
        self.assertIsInstance(txn.amount, Decimal)

    def test_stored_amount_is_decimal(self):
        self.upload(csv_file(HEADER, "2026-04-09,UNKNOWN INCOMING WIRE,9999.99,credit,FT1"))
        amount = BankTransaction.objects.values_list("amount", flat=True).get()
        self.assertEqual(amount, Decimal("9999.99"))
        self.assertIsInstance(amount, Decimal)

    def test_valid_rows_imported_when_others_invalid(self):
        response = self.upload(csv_file(
            HEADER,
            "2026-04-03,A,10.00,credit,R1",
            "2026-04-03,B,25.001,credit,R2",
            "2026-04-03,C,-5.00,debit,R3",
        ))

        self.assertEqual(response.json()["imported_count"], 2)
        self.assertEqual(response.json()["errors"], [{"line": 3, "message": "Amount '25.001' has more than 2 decimal places"}])
        self.assertEqual(set(BankTransaction.objects.values_list("bank_reference", flat=True)), {"R1", "R3"})

    def test_debit_stored_ignored_without_matches(self):
        response = self.upload(csv_file(HEADER, "2026-04-05,BANK FEE,-25.00,DEBIT,FEE1"))

        self.assertEqual(response.json()["imported_count"], 1)
        txn = BankTransaction.objects.get()
        self.assertEqual((txn.type, txn.status, txn.amount), (TransactionType.DEBIT, TransactionStatus.IGNORED, Decimal("-25.00")))
        self.assertEqual(Match.objects.count(), 0)

    def test_credits_are_not_ignored(self):
        self.upload(csv_file(HEADER, "2026-04-03,WIRE,10.00,credit,R1"))
        self.assertNotEqual(BankTransaction.objects.get().status, TransactionStatus.IGNORED)

    def test_blank_rows_ignored_without_transactions(self):
        response = self.upload(csv_file(HEADER, "", ",,,,", "2026-04-03,A,10.00,credit,R1", "   "))

        self.assertEqual(response.json(), {"imported_count": 1, "duplicate_count": 0, "duplicates": [], "errors": []})
        self.assertEqual(BankTransaction.objects.count(), 1)

    def test_header_only_file_imports_nothing(self):
        response = self.upload(csv_file(HEADER))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported_count"], 0)

    def test_import_several_thousand_rows(self):
        rows = [f"2026-04-03,WIRE {i},{i + 1}.00,credit,REF{i:05d}" for i in range(5000)]
        response = self.upload(csv_file(HEADER, *rows))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported_count"], 5000)
        self.assertEqual(BankTransaction.objects.count(), 5000)


class FileRejectionTests(ImportApiTestCase):
    def assertRejected(self, response, fragment):
        self.assertEqual(response.status_code, 400)
        self.assertIn(fragment, response.json()["detail"])
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_missing_column_rejects_file_and_saves_nothing(self):
        response = self.upload(csv_file("date,description,type,reference", "2026-04-03,A,credit,R1"))
        self.assertRejected(response, "Missing required column(s): amount.")

    def test_no_file_rejected(self):
        response = self.client.post(URL, {}, format="multipart", HTTP_X_ORGANIZATION="northwind")
        self.assertRejected(response, "'file'")

    def test_empty_file_rejected(self):
        self.assertRejected(self.upload(SimpleUploadedFile("empty.csv", b"")), "The file is empty")

    def test_non_utf8_file_rejected(self):
        content = (HEADER + "\n2026-04-03,Caf\xe9,10.00,credit,R1\n").encode("latin-1")
        self.assertRejected(self.upload(SimpleUploadedFile("latin1.csv", content)), "UTF-8")

    def test_bom_and_reordered_headers_with_extra_columns_import(self):
        content = "﻿ Reference ,Memo,TYPE,amount,Description,date\nR1,note,credit,10.00,Wire,2026-04-03\n"
        response = self.upload(SimpleUploadedFile("bom.csv", content.encode("utf-8")))
        self.assertEqual(response.json()["imported_count"], 1)


class DuplicateTests(ImportApiTestCase):
    def test_duplicate_within_same_file_skipped(self):
        response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1", "2026-04-04,B,20.00,credit,R1"))

        self.assertEqual(response.json()["imported_count"], 1)
        self.assertEqual(response.json()["duplicate_count"], 1)
        self.assertEqual(BankTransaction.objects.get().description, "A")

    def test_reupload_skips_all_rows_as_duplicates(self):
        lines = (HEADER, "2026-04-03,A,10.00,credit,R1", "2026-04-04,B,-1.00,debit,R2", "2026-04-04,C,5.00,credit,R1")
        self.upload(csv_file(*lines))

        body = self.upload(csv_file(*lines)).json()

        self.assertEqual(body["imported_count"], 0)
        self.assertEqual(body["duplicate_count"], 3)
        self.assertTrue(all(d["message"].endswith("already imported for this organization") for d in body["duplicates"]))
        self.assertEqual(BankTransaction.objects.count(), 2)

    def test_duplicate_report_includes_line_and_explanation(self):
        make_transaction(self.northwind, reference="OLD1")
        body = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,OLD1", "2026-04-03,B,10.00,credit,N1",
                                    "2026-04-03,C,10.00,credit,N1")).json()

        self.assertEqual(body["duplicates"], [
            {"line": 2, "reference": "OLD1", "message": "Duplicate bank reference OLD1: already imported for this organization"},
            {"line": 4, "reference": "N1", "message": "Duplicate bank reference N1: same as line 3 in this file"},
        ])

    def test_row_matching_earlier_invalid_row_is_not_duplicate(self):
        body = self.upload(csv_file(HEADER, "2026-04-31,A,10.00,credit,R1", "2026-04-03,B,10.00,credit,R1")).json()

        self.assertEqual((body["imported_count"], body["duplicate_count"]), (1, 0))
        self.assertEqual([e["line"] for e in body["errors"]], [2])

    def test_duplicate_detection_uses_exact_trimmed_reference(self):
        body = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,FT20260403A",
                                    "2026-04-03,B,10.00,credit, FT20260403A ",
                                    "2026-04-03,C,10.00,credit,ft20260403a")).json()

        self.assertEqual((body["imported_count"], body["duplicate_count"]), (2, 1))
        self.assertEqual(body["duplicates"][0]["line"], 3)

    def test_same_reference_allowed_in_other_organization(self):
        self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="northwind")
        body = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="bluebird").json()

        self.assertEqual((body["imported_count"], body["duplicate_count"]), (1, 0))
        self.assertEqual(BankTransaction.objects.filter(bank_reference="R1").count(), 2)

    def test_unique_constraint_conflict_returns_409_and_saves_nothing(self):
        # Simulates a concurrent import that committed R1 after our duplicate check ran.
        make_transaction(self.northwind, reference="R1")
        empty = BankTransaction.objects.none()
        with mock.patch("reconciliation.services.importer.BankTransaction.objects.filter", return_value=empty):
            response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R0", "2026-04-03,B,10.00,credit,R1"))

        self.assertEqual(response.status_code, 409)
        self.assertIn("Nothing was saved", response.json()["detail"])
        self.assertEqual(list(BankTransaction.objects.values_list("bank_reference", flat=True)), ["R1"])


class OrganizationScopingTests(ImportApiTestCase):
    def test_missing_org_header_returns_400(self):
        response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org=None)
        self.assertEqual(response.status_code, 400)
        self.assertIn("X-Organization", response.json()["detail"])
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_blank_org_header_returns_400(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org=value)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_unknown_org_returns_404(self):
        response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_org_slug_is_case_sensitive(self):
        response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="Northwind")
        self.assertEqual(response.status_code, 404)

    def test_import_scoped_to_header_org(self):
        make_transaction(self.bluebird, reference="R1")

        body = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="northwind").json()

        # bluebird's R1 is neither a duplicate for northwind nor modified.
        self.assertEqual((body["imported_count"], body["duplicate_count"]), (1, 0))
        self.assertEqual(BankTransaction.objects.filter(organization=self.northwind).count(), 1)
        self.assertEqual(BankTransaction.objects.filter(organization=self.bluebird).count(), 1)

    def test_header_value_is_trimmed(self):
        response = self.upload(csv_file(HEADER, "2026-04-03,A,10.00,credit,R1"), org="  northwind  ")
        self.assertEqual(response.status_code, 200)
