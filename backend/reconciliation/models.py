"""
FundFlow domain models (PRD §4, integrity rules PRD §13.1; IDs DB1–DB15 are referenced inline).

Every tenant table carries its own `organization` foreign key so that queries can be scoped with a
plain `filter(organization=org)`. Ordinary foreign keys cannot guarantee that related rows belong
to the same organization, so `clean()` enforces that (PRD SV1); services must call `full_clean()`
or `assert_same_organization()` before creating line items and matches.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from .constants import LineItemStatus, MatchStatus, TransactionStatus, TransactionType

MONEY = {"max_digits": 14, "decimal_places": 2}
ZERO = Decimal("0.00")


def assert_same_organization(organization_id, **related):
    """Raise ValidationError if any related object belongs to a different organization."""
    errors = {
        field: "Must belong to the same organization."
        for field, obj in related.items()
        if obj is not None and obj.organization_id != organization_id
    }
    if errors:
        raise ValidationError(errors)


class Organization(models.Model):
    slug = models.SlugField(max_length=50, unique=True)  # DB9
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.slug


class InvestorEntity(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="investor_entities")
    name = models.CharField(max_length=200)

    class Meta:
        verbose_name_plural = "investor entities"
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="investor_unique_name_per_org"),  # DB13
        ]

    def __str__(self):
        return self.name


class CapitalCall(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="capital_calls")
    number = models.PositiveIntegerField()
    call_date = models.DateField()
    due_date = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="capital_call_unique_number_per_org"),  # DB10
        ]

    def __str__(self):
        return f"{self.organization_id}#{self.number}"


class LineItem(models.Model):
    """One LP's share of one capital call. Remaining is derived, never stored."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="line_items")
    capital_call = models.ForeignKey(CapitalCall, on_delete=models.PROTECT, related_name="line_items")
    investor_entity = models.ForeignKey(InvestorEntity, on_delete=models.PROTECT, related_name="line_items")
    # Intentionally not unique (D-28): a shared reference yields several matching candidates.
    expected_reference = models.CharField(max_length=50)
    amount_called = models.DecimalField(**MONEY)
    amount_funded = models.DecimalField(**MONEY, default=ZERO)
    status = models.CharField(max_length=20, choices=LineItemStatus.choices, default=LineItemStatus.OPEN)

    class Meta:
        constraints = [
            # DB11: one LP's share of one capital call.
            models.UniqueConstraint(fields=["capital_call", "investor_entity"], name="line_item_unique_call_investor"),
            # DB2: funding is never negative and never exceeds the amount called.
            models.CheckConstraint(
                condition=Q(amount_called__gt=0)
                & Q(amount_funded__gte=0)
                & Q(amount_funded__lte=F("amount_called")),
                name="line_item_funding_within_called",
            ),
            # DB3 + DB6: status always agrees with the amounts.
            models.CheckConstraint(
                condition=Q(status=LineItemStatus.OPEN, amount_funded=0)
                | Q(status=LineItemStatus.PARTIALLY_FUNDED, amount_funded__gt=0, amount_funded__lt=F("amount_called"))
                | Q(status=LineItemStatus.FUNDED, amount_funded=F("amount_called")),
                name="line_item_status_matches_amounts",
            ),
        ]

    def __str__(self):
        return self.expected_reference

    @property
    def amount_remaining(self):
        return self.amount_called - self.amount_funded

    def recompute_status(self):
        """Set status from the amounts; call in the same DB transaction as any funding change."""
        if self.amount_funded == ZERO:
            self.status = LineItemStatus.OPEN
        elif self.amount_funded < self.amount_called:
            self.status = LineItemStatus.PARTIALLY_FUNDED
        else:
            self.status = LineItemStatus.FUNDED

    def clean(self):
        assert_same_organization(
            self.organization_id,
            capital_call=self.capital_call if self.capital_call_id else None,
            investor_entity=self.investor_entity if self.investor_entity_id else None,
        )


class BankTransaction(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="bank_transactions")
    date = models.DateField()
    description = models.TextField()
    amount = models.DecimalField(**MONEY)  # signed: credits positive, debits negative
    type = models.CharField(max_length=10, choices=TransactionType.choices)
    bank_reference = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=TransactionStatus.choices)
    review_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # DB1: a bank reference is imported at most once per organization.
            models.UniqueConstraint(fields=["organization", "bank_reference"], name="bank_txn_unique_reference_per_org"),
            # DB4: non-zero amount whose sign agrees with the type (also restricts type values, DB6).
            models.CheckConstraint(
                condition=Q(type=TransactionType.CREDIT, amount__gt=0) | Q(type=TransactionType.DEBIT, amount__lt=0),
                name="bank_txn_amount_sign_matches_type",
            ),
            # DB5: debits are always stored as ignored.
            models.CheckConstraint(
                condition=~Q(type=TransactionType.DEBIT) | Q(status=TransactionStatus.IGNORED),
                name="bank_txn_debit_is_ignored",
            ),
            # DB6
            models.CheckConstraint(
                condition=Q(status__in=TransactionStatus.values), name="bank_txn_status_valid"
            ),
            # DB15: every needs_review transaction explains why.
            models.CheckConstraint(
                condition=~Q(status=TransactionStatus.NEEDS_REVIEW) | ~Q(review_reason=""),
                name="bank_txn_needs_review_has_reason",
            ),
            # Required CSV fields (brief §5) are never stored empty.
            models.CheckConstraint(
                condition=~Q(description="") & ~Q(bank_reference=""), name="bank_txn_required_fields_not_empty"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-date", "-id"], name="bank_txn_org_newest_first"),
        ]

    def __str__(self):
        return self.bank_reference


class Match(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="matches")
    transaction = models.ForeignKey(BankTransaction, on_delete=models.PROTECT, related_name="matches")
    line_item = models.ForeignKey(LineItem, on_delete=models.PROTECT, related_name="matches")
    status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.PROPOSED)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "matches"
        constraints = [
            # DB7: a line item is proposed at most once per transaction.
            models.UniqueConstraint(fields=["transaction", "line_item"], name="match_unique_transaction_line_item"),
            # DB8: at most one approved match per transaction, so one payment can never fund two line items.
            models.UniqueConstraint(
                fields=["transaction"],
                condition=Q(status=MatchStatus.APPROVED),
                name="match_one_approved_per_transaction",
            ),
            # DB6
            models.CheckConstraint(condition=Q(status__in=MatchStatus.values), name="match_status_valid"),
            # DB15
            models.CheckConstraint(condition=~Q(reason=""), name="match_reason_not_empty"),
        ]

    def __str__(self):
        return f"{self.transaction_id} -> {self.line_item_id} ({self.status})"

    def clean(self):
        assert_same_organization(
            self.organization_id,
            transaction=self.transaction if self.transaction_id else None,
            line_item=self.line_item if self.line_item_id else None,
        )
