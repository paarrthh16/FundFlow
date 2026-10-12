"""
Approve / reject / ignore (brief §7, PRD §7–§8, integrity SV3–SV7).

Every action runs in one database transaction and takes row locks in a fixed order:
bank transaction -> its match rows (by id) -> line item. No path locks a line item before a
transaction, so concurrent actions serialise without deadlocks. All state checks are made on
rows re-read *after* the locks are held; nothing from the client is trusted except the ID.
"""
from django.db import transaction

from ..constants import MatchStatus, TransactionStatus, TransactionType
from ..exceptions import ConflictError, NotFoundError
from ..models import BankTransaction, LineItem, Match
from .matching import money

ACTIONABLE_STATUSES = (TransactionStatus.SUGGESTED, TransactionStatus.NEEDS_REVIEW)
ALL_REJECTED_REASON = "All proposed matches were rejected; review required"


@transaction.atomic
def approve_match(organization, match_id):
    txn, matches, match = _lock_for_match(organization, match_id)
    if match.status != MatchStatus.PROPOSED:
        raise ConflictError(f"Match {match.pk} is {match.status}; only proposed matches can be approved.")
    _require_actionable_credit(txn, "approved")

    line_item = _lock_line_item(organization, match)
    if line_item.capital_call.call_date > txn.date:
        raise ConflictError(f"Line item {line_item.expected_reference} was not yet called on {txn.date}.")
    if txn.amount > line_item.amount_remaining:
        raise ConflictError(
            f"Amount {money(txn.amount)} exceeds the remaining {money(line_item.amount_remaining)} "
            f"on line item {line_item.expected_reference}."
        )

    line_item.amount_funded += txn.amount
    line_item.recompute_status()
    line_item.save(update_fields=["amount_funded", "status"])

    for other in matches:
        if other.pk != match.pk and other.status != MatchStatus.REJECTED:
            other.status = MatchStatus.REJECTED
            other.save(update_fields=["status"])
    match.status = MatchStatus.APPROVED
    match.save(update_fields=["status"])

    txn.status = TransactionStatus.RECONCILED
    txn.save(update_fields=["status"])
    return txn


@transaction.atomic
def reject_match(organization, match_id):
    txn, matches, match = _lock_for_match(organization, match_id)
    if match.status != MatchStatus.PROPOSED:
        raise ConflictError(f"Match {match.pk} is {match.status}; only proposed matches can be rejected.")
    _require_actionable_credit(txn, "changed")

    match.status = MatchStatus.REJECTED
    match.save(update_fields=["status"])

    if not any(m.status == MatchStatus.PROPOSED for m in matches):
        txn.status = TransactionStatus.NEEDS_REVIEW
        txn.review_reason = ALL_REJECTED_REASON
        txn.save(update_fields=["status", "review_reason"])
    return txn


@transaction.atomic
def ignore_transaction(organization, transaction_id):
    txn = _lock_transaction(organization, transaction_id)
    if txn.status == TransactionStatus.RECONCILED:
        raise ConflictError(f"Transaction {txn.pk} is reconciled; it cannot be ignored.")
    _require_actionable_credit(txn, "ignored")

    for match in _lock_matches(txn):
        if match.status == MatchStatus.PROPOSED:
            match.status = MatchStatus.REJECTED
            match.save(update_fields=["status"])
    txn.status = TransactionStatus.IGNORED
    txn.save(update_fields=["status"])
    return txn


def _lock_for_match(organization, match_id):
    """Find the match within the organization, then lock its transaction and all its matches."""
    transaction_id = (
        Match.objects.filter(pk=match_id, transaction__organization=organization)
        .values_list("transaction_id", flat=True)
        .first()
    )
    if transaction_id is None:
        raise NotFoundError("Match not found.")
    txn = _lock_transaction(organization, transaction_id)
    matches = _lock_matches(txn)
    match = next(m for m in matches if m.pk == int(match_id))  # re-read under lock
    if match.organization_id != organization.pk:
        raise ConflictError(f"Match {match.pk} does not belong to this organization.")
    return txn, matches, match


def _lock_transaction(organization, transaction_id):
    try:
        return BankTransaction.objects.select_for_update().get(pk=transaction_id, organization=organization)
    except BankTransaction.DoesNotExist:
        raise NotFoundError("Transaction not found.") from None


def _lock_matches(txn):
    return list(Match.objects.select_for_update().filter(transaction=txn).order_by("pk"))


def _lock_line_item(organization, match):
    try:
        return (
            LineItem.objects.select_for_update(of=("self",))
            .select_related("capital_call")
            .get(pk=match.line_item_id, organization=organization)
        )
    except LineItem.DoesNotExist:
        # Only reachable if a cross-organization match was written by bypassing the services.
        raise ConflictError(f"Match {match.pk} does not belong to this organization's line items.") from None


def _require_actionable_credit(txn, verb):
    if txn.type != TransactionType.CREDIT or txn.status not in ACTIONABLE_STATUSES:
        raise ConflictError(f"Transaction {txn.pk} is {txn.status} ({txn.type}); it cannot be {verb}.")
