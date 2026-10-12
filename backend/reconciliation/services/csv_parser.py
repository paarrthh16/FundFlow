"""
Bank statement CSV parsing and validation (brief §5, PRD §5.1–5.2, decisions D-01–D-06, D-29–D-32).

Pure: no database access. File-level problems raise InvalidFileError (nothing may be saved);
row-level problems become RowErrors so the remaining valid rows can still be imported.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..constants import TransactionType
from ..exceptions import InvalidFileError

REQUIRED_COLUMNS = ("date", "description", "amount", "type", "reference")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Optional "-", integer digits either ungrouped or grouped in threes with ",", optional fraction.
# The fraction length is checked separately so "25.001" gets a precise message instead of "malformed".
AMOUNT_RE = re.compile(r"^(?P<sign>-)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?$")
MAX_DECIMAL_PLACES = 2
MAX_INTEGER_DIGITS = 12  # DecimalField(max_digits=14, decimal_places=2)
MAX_REFERENCE_LENGTH = 100  # BankTransaction.bank_reference max_length


@dataclass(frozen=True)
class ParsedRow:
    line: int
    date: date
    description: str
    amount: Decimal
    type: str
    reference: str


@dataclass(frozen=True)
class RowError:
    line: int
    message: str


@dataclass
class ParseResult:
    rows: list
    errors: list


def parse_statement(content: bytes) -> ParseResult:
    text = _decode(content)
    records = _records_with_line_numbers(text)

    header_line, header = _first_non_blank(records)
    columns = _column_positions(header)

    rows, errors = [], []
    for line, record in records:
        if _is_blank(record):
            continue  # D-05: blank rows create nothing and are not reported
        if len(record) > len(header):
            errors.append(RowError(line, f"Row has {len(record)} values but the header has {len(header)} columns"))
            continue
        values = {name: (record[i] if i < len(record) else "").strip() for name, i in columns.items()}
        row, messages = _validate(line, values)
        if messages:
            errors.append(RowError(line, "; ".join(messages)))
        else:
            rows.append(row)
    return ParseResult(rows=rows, errors=errors)


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")  # removes a leading BOM if present (D-01)
    except UnicodeDecodeError:
        raise InvalidFileError("The file must be UTF-8 encoded text.") from None


def _records_with_line_numbers(text):
    """Return (line, record) pairs; `line` is the physical line on which each record starts."""
    reader = csv.reader(io.StringIO(text, newline=""))
    records, previous_end = [], 0
    try:
        for record in reader:
            records.append((previous_end + 1, record))
            previous_end = reader.line_num
    except csv.Error as exc:
        raise InvalidFileError(f"The file is not valid CSV (line {reader.line_num}): {exc}") from None
    return iter(records)


def _first_non_blank(records):
    for line, record in records:
        if not _is_blank(record):
            return line, record
    raise InvalidFileError(
        "The file is empty. Expected a header row with columns: " + ", ".join(REQUIRED_COLUMNS) + "."
    )


def _column_positions(header):
    names = [name.strip().lower() for name in header]  # D-02
    repeated = [col for col in REQUIRED_COLUMNS if names.count(col) > 1]
    if repeated:
        raise InvalidFileError("Repeated column(s) in header: " + ", ".join(repeated) + ".")
    missing = [col for col in REQUIRED_COLUMNS if col not in names]
    if missing:
        raise InvalidFileError("Missing required column(s): " + ", ".join(missing) + ".")
    return {col: names.index(col) for col in REQUIRED_COLUMNS}  # extra columns are ignored (D-03)


def _is_blank(record):
    return all(not value.strip() for value in record)


def _validate(line, values):
    messages = []
    txn_date = _parse_date(values["date"], messages)

    description = values["description"]
    if not description:
        messages.append("Description is required")

    amount = _parse_amount(values["amount"], messages)

    txn_type = values["type"].lower()
    if txn_type not in TransactionType.values:
        messages.append(f"Invalid type '{values['type']}': expected credit or debit")
        txn_type = None

    reference = values["reference"]
    if not reference:
        messages.append("Reference is required")
    elif len(reference) > MAX_REFERENCE_LENGTH:
        messages.append(f"Reference is longer than {MAX_REFERENCE_LENGTH} characters")

    if amount is not None and txn_type == TransactionType.CREDIT and amount < 0:
        messages.append("Credit amount must be positive")
    if amount is not None and txn_type == TransactionType.DEBIT and amount > 0:
        messages.append("Debit amount must be negative")

    if messages:
        return None, messages
    return ParsedRow(line, txn_date, description, amount, txn_type, reference), []


def _parse_date(raw, messages):
    if not raw:
        messages.append("Date is required")
        return None
    if DATE_RE.match(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    messages.append(f"Invalid date '{raw}': expected a real calendar date in YYYY-MM-DD format")
    return None


def _parse_amount(raw, messages):
    """Return a Decimal, or None after appending a message. Never rounds (D-04)."""
    if not raw:
        messages.append("Amount is required")
        return None
    match = AMOUNT_RE.match(raw)
    if not match:
        messages.append(f"Invalid amount '{raw}'")
        return None
    if len(match["frac"] or "") > MAX_DECIMAL_PLACES:
        messages.append(f"Amount '{raw}' has more than {MAX_DECIMAL_PLACES} decimal places")
        return None
    integer_digits = match["int"].replace(",", "").lstrip("0")
    if len(integer_digits) > MAX_INTEGER_DIGITS:
        messages.append(f"Amount '{raw}' has more than {MAX_INTEGER_DIGITS} integer digits")
        return None
    amount = Decimal(raw.replace(",", ""))
    if amount == 0:
        messages.append("Amount must not be zero")
        return None
    return amount
