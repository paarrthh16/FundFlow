"""Pure parser tests (no database). Line numbers count the header as line 1 (brief §5)."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from reconciliation.exceptions import InvalidFileError
from reconciliation.services.csv_parser import parse_statement

HEADER = "date,description,amount,type,reference"


def parse(*lines, newline="\n"):
    return parse_statement(newline.join(lines).encode("utf-8"))


def errors_by_line(result):
    return {e.line: e.message for e in result.errors}


class FileLevelTests(SimpleTestCase):
    def test_missing_column_raises_invalid_file(self):
        with self.assertRaisesMessage(InvalidFileError, "Missing required column(s): amount, reference."):
            parse("date,description,type", "2026-04-03,X,credit")

    def test_empty_file_rejected(self):
        for content in (b"", b"\n\n", b" , ,\n"):
            with self.subTest(content=content), self.assertRaises(InvalidFileError):
                parse_statement(content)

    def test_non_utf8_file_rejected(self):
        content = (HEADER + "\n2026-04-03,Caf\xe9 wire,10.00,credit,R1\n").encode("latin-1")
        with self.assertRaisesMessage(InvalidFileError, "UTF-8"):
            parse_statement(content)

    def test_repeated_required_header_rejected(self):
        with self.assertRaisesMessage(InvalidFileError, "Repeated column(s) in header: amount."):
            parse("date,description,amount,type,reference,Amount")

    def test_header_only_file_has_no_rows(self):
        result = parse(HEADER)
        self.assertEqual((result.rows, result.errors), ([], []))


class HeaderTests(SimpleTestCase):
    def test_bom_is_stripped_from_header(self):
        result = parse_statement(("﻿" + HEADER + "\n2026-04-03,X,10.00,credit,R1\n").encode("utf-8"))
        self.assertEqual(len(result.rows), 1)

    def test_header_names_trimmed_and_case_insensitive(self):
        result = parse(" DATE , Description,AMOUNT ,Type, reference ", "2026-04-03,X,10.00,credit,R1")
        self.assertEqual(len(result.rows), 1)

    def test_extra_columns_ignored_and_order_independent(self):
        result = parse("reference,memo,type,amount,description,date", "R1,ignored text,credit,10.00,Wire,2026-04-03")
        row = result.rows[0]
        self.assertEqual(
            (row.reference, row.type, row.amount, row.description, row.date),
            ("R1", "credit", Decimal("10.00"), "Wire", date(2026, 4, 3)),
        )


class RowValidationTests(SimpleTestCase):
    def assertRowError(self, row, expected_fragment):
        result = parse(HEADER, row)
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].line, 2)
        self.assertIn(expected_fragment, result.errors[0].message)

    def test_amount_with_thousands_separator_parsed_as_decimal(self):
        row = parse(HEADER, '2026-04-03,X,"25,000.00",credit,R1').rows[0]
        self.assertEqual(row.amount, Decimal("25000.00"))
        self.assertIsInstance(row.amount, Decimal)

    def test_valid_amount_formats(self):
        for raw, expected in [("1000", "1000"), ('"1,000.5"', "1000.5"), ("0.01", "0.01"),
                              ('"999,999,999,999.99"', "999999999999.99")]:
            with self.subTest(raw=raw):
                self.assertEqual(parse(HEADER, f"2026-04-03,X,{raw},credit,R1").rows[0].amount, Decimal(expected))

    def test_malformed_amounts_are_row_errors(self):
        cases = {
            '"1,00"': "Invalid amount",
            "25.001": "more than 2 decimal places",
            "1000000000000": "more than 12 integer digits",
            "$100": "Invalid amount",
            "(25.00)": "Invalid amount",
            "+5": "Invalid amount",
            "1e3": "Invalid amount",
            "abc": "Invalid amount",
            ".50": "Invalid amount",
            "10.": "Invalid amount",
            "": "Amount is required",
        }
        for raw, fragment in cases.items():
            with self.subTest(raw=raw):
                self.assertRowError(f"2026-04-03,X,{raw},credit,R1", fragment)

    def test_excess_precision_is_never_rounded(self):
        result = parse(HEADER, "2026-04-03,X,25.001,credit,R1", "2026-04-03,X,25.005,credit,R2")
        self.assertEqual(result.rows, [])
        self.assertEqual([e.line for e in result.errors], [2, 3])

    def test_zero_amount_is_row_error(self):
        for raw in ("0", "0.00", "-0.00"):
            with self.subTest(raw=raw):
                self.assertRowError(f"2026-04-03,X,{raw},credit,R1", "must not be zero")

    def test_credit_with_negative_amount_is_row_error(self):
        self.assertRowError("2026-04-03,X,-15000.00,credit,R1", "Credit amount must be positive")

    def test_debit_with_positive_amount_is_row_error(self):
        self.assertRowError("2026-04-05,FEE,25.00,debit,R1", "Debit amount must be negative")

    def test_invalid_calendar_date_is_row_error(self):
        for raw in ("2026-04-31", "2026-02-30", "2026-13-01"):
            with self.subTest(raw=raw):
                self.assertRowError(f"{raw},X,10.00,credit,R1", "Invalid date")

    def test_wrong_date_format_is_row_error(self):
        for raw in ("2026-4-3", "03/04/2026", "20260403", "2026-04-03T00:00"):
            with self.subTest(raw=raw):
                self.assertRowError(f"{raw},X,10.00,credit,R1", "Invalid date")

    def test_type_case_insensitive_and_invalid_type_rejected(self):
        result = parse(HEADER, "2026-04-03,X,10.00,CREDIT,R1", "2026-04-03,FEE,-1.00,Debit,R2")
        self.assertEqual([r.type for r in result.rows], ["credit", "debit"])
        self.assertRowError("2026-04-03,X,10.00,refund,R1", "Invalid type 'refund'")

    def test_blank_description_or_reference_is_row_error(self):
        self.assertRowError("2026-04-03,   ,10.00,credit,R1", "Description is required")
        self.assertRowError("2026-04-03,X,10.00,credit,  ", "Reference is required")

    def test_fields_trimmed_before_validation(self):
        row = parse(HEADER, ' 2026-04-08 ,"  wire incoming meridian partners  ", 15000.00 , credit ,  FT1 ').rows[0]
        self.assertEqual(
            (row.date, row.description, row.amount, row.type, row.reference),
            (date(2026, 4, 8), "wire incoming meridian partners", Decimal("15000.00"), "credit", "FT1"),
        )

    def test_several_problems_reported_as_one_message(self):
        result = parse(HEADER, "2026-04-31,,abc,refund,")
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(
            result.errors[0].message,
            "Invalid date '2026-04-31': expected a real calendar date in YYYY-MM-DD format; "
            "Description is required; Invalid amount 'abc'; Invalid type 'refund': expected credit or debit; "
            "Reference is required",
        )

    def test_row_with_more_values_than_header_is_row_error(self):
        self.assertRowError("2026-04-03,X,10.00,credit,R1,extra", "Row has 6 values but the header has 5 columns")

    def test_overlong_reference_is_row_error(self):
        self.assertRowError(f"2026-04-03,X,10.00,credit,{'R' * 101}", "longer than 100 characters")


class LineNumberTests(SimpleTestCase):
    def test_line_numbers_count_header_and_blank_lines(self):
        result = parse(
            HEADER,                            # 1
            "2026-04-03,A,10.00,credit,R1",    # 2
            "",                                # 3 blank
            " , , , , ",                       # 4 blank after trimming
            "2026-04-31,B,10.00,credit,R2",    # 5 invalid
            "2026-04-03,C,10.00,credit,R3",    # 6
            newline="\r\n",
        )
        self.assertEqual([r.line for r in result.rows], [2, 6])
        self.assertEqual(list(errors_by_line(result)), [5])

    def test_quoted_amount_with_comma_reports_correct_line(self):
        result = parse(HEADER, '2026-04-03,A,"25,000.00",credit,R1', '2026-04-03,B,"1,00",credit,R2')
        self.assertEqual(list(errors_by_line(result)), [3])

    def test_quoted_newline_keeps_physical_line_numbers(self):
        result = parse(HEADER, '2026-04-03,"multi\nline",10.00,credit,R1', "2026-04-31,B,10.00,credit,R2")
        self.assertEqual(result.rows[0].line, 2)
        self.assertEqual(list(errors_by_line(result)), [4])
