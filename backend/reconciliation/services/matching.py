"""
Import-time matching of a credit to capital-call line items (brief §6, PRD §6, D-10–D-15).

Pure: `match_credit` works on in-memory `Candidate`s and never touches the database or funding.
The caller must supply only line items of the transaction's organization; eligibility by remaining
amount and call date is applied here. Suggestions are computed once, at import.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..constants import TransactionStatus
from .normalization import name_found, normalize_name, normalize_reference

NO_MATCH_REASON = "No eligible line item matched by reference or by name and exact amount"


@dataclass(frozen=True)
class Candidate:
    line_item: object  # the LineItem (or any object) to propose; opaque to matching
    reference: str
    investor_name: str
    call_date: date
    remaining: Decimal
    normalized_reference: str = field(init=False)
    name_words: tuple = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "normalized_reference", normalize_reference(self.reference))
        object.__setattr__(self, "name_words", normalize_name(self.investor_name))

    @classmethod
    def from_line_item(cls, item):
        return cls(
            line_item=item,
            reference=item.expected_reference,
            investor_name=item.investor_entity.name,
            call_date=item.capital_call.call_date,
            remaining=item.amount_remaining,
        )


@dataclass(frozen=True)
class Proposal:
    candidate: Candidate
    reason: str


@dataclass(frozen=True)
class MatchOutcome:
    status: str
    review_reason: str = ""
    proposals: tuple = ()


def money(amount):
    return f"{amount:,.2f}"


def match_credit(amount, txn_date, description, bank_reference, candidates):
    eligible = [c for c in candidates if c.remaining > 0 and c.call_date <= txn_date]

    # Step 1: reference check against the reference-normalised description and bank reference.
    description_ref = normalize_reference(description)
    bank_ref = normalize_reference(bank_reference)
    by_reference = [
        c for c in eligible
        if c.normalized_reference and (c.normalized_reference in description_ref or c.normalized_reference in bank_ref)
    ]
    if len(by_reference) == 1:
        return _single_reference(amount, by_reference[0])
    if by_reference:
        listing = ", ".join(f"{c.reference} {c.investor_name}" for c in by_reference)
        return MatchOutcome(
            status=TransactionStatus.NEEDS_REVIEW,
            review_reason=f"References for {len(by_reference)} line items found ({listing}); choose one",
            proposals=tuple(Proposal(c, f"Reference {c.reference} found") for c in by_reference),
        )

    # Step 2: investor name found as whole words AND amount exactly equals remaining.
    description_words = normalize_name(description, drop_suffix=False)
    by_name = [c for c in eligible if c.remaining == amount and name_found(c.name_words, description_words)]
    proposals = tuple(
        Proposal(c, f"Name {' '.join(c.name_words)} found; amount equals remaining {money(c.remaining)}")
        for c in by_name
    )
    if len(by_name) == 1:
        return MatchOutcome(status=TransactionStatus.SUGGESTED, proposals=proposals)
    if by_name:
        references = ", ".join(c.reference for c in by_name)
        return MatchOutcome(
            status=TransactionStatus.NEEDS_REVIEW,
            review_reason=f"Name and amount match {len(by_name)} line items ({references}); choose one",
            proposals=proposals,
        )
    return MatchOutcome(status=TransactionStatus.NEEDS_REVIEW, review_reason=NO_MATCH_REASON)


def _single_reference(amount, candidate):
    if amount > candidate.remaining:
        return MatchOutcome(
            status=TransactionStatus.NEEDS_REVIEW,
            review_reason=(
                f"Reference {candidate.reference} found, but amount {money(amount)} exceeds remaining "
                f"{money(candidate.remaining)} (overpayment); no match proposed"
            ),
        )
    if amount == candidate.remaining:
        reason = f"Reference {candidate.reference} found; amount equals remaining {money(candidate.remaining)}"
    else:
        reason = (
            f"Reference {candidate.reference} found; amount {money(amount)} is less than remaining "
            f"{money(candidate.remaining)} (partial payment)"
        )
    return MatchOutcome(status=TransactionStatus.SUGGESTED, proposals=(Proposal(candidate, reason),))
