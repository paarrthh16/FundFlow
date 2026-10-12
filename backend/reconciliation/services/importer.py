"""
Bank statement import (brief §5, PRD §5.3–5.5 and §6, decisions D-07–D-10).

Parse first: a file-level problem raises InvalidFileError before any database access. Then, in one
database transaction with the organization row locked (imports for one organization are serialised):
detect duplicates, run import-time matching for credits, bulk-insert transactions and then their
proposed matches. Matching never changes funding. The unique constraint on
(organization, bank_reference) is the final safeguard; if it still fires, nothing is saved and
the caller gets a ConflictError (409).
"""
from dataclasses import asdict, dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import F

from ..constants import MatchStatus, TransactionStatus, TransactionType
from ..exceptions import ConflictError
from ..models import BankTransaction, LineItem, Match, Organization, assert_same_organization
from .csv_parser import parse_statement
from .matching import Candidate, match_credit

BANK_REFERENCE_CONSTRAINT = "bank_txn_unique_reference_per_org"
BATCH_SIZE = 1000


@dataclass
class DuplicateRow:
    line: int
    reference: str
    message: str


@dataclass
class ImportSummary:
    imported_count: int = 0
    duplicates: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def duplicate_count(self):
        return len(self.duplicates)

    def as_dict(self):
        return {
            "imported_count": self.imported_count,
            "duplicate_count": self.duplicate_count,
            "duplicates": [asdict(d) for d in self.duplicates],
            "errors": [asdict(e) for e in self.errors],
        }


def import_statement(organization, content: bytes) -> ImportSummary:
    parsed = parse_statement(content)  # may raise InvalidFileError; nothing touched yet
    summary = ImportSummary(errors=parsed.errors)

    try:
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization.pk)
            candidates = _load_candidates(organization)
            new_transactions, proposals = _new_transactions(organization, parsed.rows, candidates, summary)
            BankTransaction.objects.bulk_create(new_transactions, batch_size=BATCH_SIZE)
            Match.objects.bulk_create(_build_matches(organization, proposals), batch_size=BATCH_SIZE)
    except IntegrityError as exc:
        if _violated_constraint(exc) != BANK_REFERENCE_CONSTRAINT:
            raise  # any other violation is a bug, not a user error
        raise ConflictError(
            "A bank reference in this file was imported concurrently. Nothing was saved; please upload again."
        ) from None

    summary.imported_count = len(new_transactions)
    return summary


def _load_candidates(organization):
    """This organization's line items with a remaining amount, loaded once per import (PRD §6.2)."""
    items = (
        LineItem.objects.filter(organization=organization, amount_funded__lt=F("amount_called"))
        .select_related("capital_call", "investor_entity")
        .order_by("capital_call__call_date", "expected_reference", "id")
    )
    return [Candidate.from_line_item(item) for item in items]


def _new_transactions(organization, rows, candidates, summary):
    """
    Skip duplicates (database first, then earlier in this file), match credits, and return the
    unsaved transactions plus (transaction, proposal) pairs to persist after the transactions exist.
    """
    already_imported = set(
        BankTransaction.objects.filter(
            organization=organization, bank_reference__in={row.reference for row in rows}
        ).values_list("bank_reference", flat=True)
    )
    first_line_for_reference = {}
    new_transactions, proposals = [], []

    for row in rows:
        if row.reference in already_imported:
            summary.duplicates.append(DuplicateRow(
                row.line, row.reference,
                f"Duplicate bank reference {row.reference}: already imported for this organization",
            ))
            continue
        if row.reference in first_line_for_reference:
            summary.duplicates.append(DuplicateRow(
                row.line, row.reference,
                f"Duplicate bank reference {row.reference}: same as line "
                f"{first_line_for_reference[row.reference]} in this file",
            ))
            continue
        first_line_for_reference[row.reference] = row.line

        txn = BankTransaction(
            organization=organization,
            date=row.date,
            description=row.description,
            amount=row.amount,
            type=row.type,
            bank_reference=row.reference,
        )
        if row.type == TransactionType.DEBIT:
            txn.status = TransactionStatus.IGNORED  # debits never go through matching
        else:
            outcome = match_credit(row.amount, row.date, row.description, row.reference, candidates)
            txn.status, txn.review_reason = outcome.status, outcome.review_reason
            proposals.extend((txn, proposal) for proposal in outcome.proposals)
        new_transactions.append(txn)
    return new_transactions, proposals


def _build_matches(organization, proposals):
    """bulk_create skips Model.clean(), so check tenant consistency explicitly (PRD SV1)."""
    matches = []
    for txn, proposal in proposals:
        line_item = proposal.candidate.line_item
        assert_same_organization(organization.pk, transaction=txn, line_item=line_item)
        matches.append(Match(
            organization=organization,
            transaction=txn,
            line_item=line_item,
            status=MatchStatus.PROPOSED,
            reason=proposal.reason,
        ))
    return matches


def _violated_constraint(exc):
    diag = getattr(exc.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None)
