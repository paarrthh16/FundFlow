"""Small factories for tests. Defaults produce valid rows; override only what a test cares about."""
from datetime import date
from decimal import Decimal

from reconciliation.constants import MatchStatus, TransactionStatus, TransactionType
from reconciliation.models import (
    BankTransaction,
    CapitalCall,
    InvestorEntity,
    LineItem,
    Match,
    Organization,
)


def make_org(slug="acme", name="Acme Capital"):
    return Organization.objects.create(slug=slug, name=name)


def make_line_item(org, *, number=1, investor="Test Investor LLC", reference="CC-9001",
                   called="1000.00", call_date=date(2026, 3, 25)):
    call, _ = CapitalCall.objects.get_or_create(
        organization=org, number=number, defaults={"call_date": call_date, "due_date": date(2026, 4, 30)}
    )
    entity, _ = InvestorEntity.objects.get_or_create(organization=org, name=investor)
    return LineItem.objects.create(
        organization=org, capital_call=call, investor_entity=entity,
        expected_reference=reference, amount_called=Decimal(called),
    )


def make_transaction(org, *, reference="FT0001", amount="1000.00", type=TransactionType.CREDIT,
                     status=TransactionStatus.SUGGESTED, review_reason="", description="WIRE INCOMING"):
    return BankTransaction.objects.create(
        organization=org, date=date(2026, 4, 3), description=description, amount=Decimal(amount),
        type=type, bank_reference=reference, status=status, review_reason=review_reason,
    )


def make_match(transaction, line_item, *, status=MatchStatus.PROPOSED, reason="Reference found"):
    return Match.objects.create(
        organization=transaction.organization, transaction=transaction, line_item=line_item,
        status=status, reason=reason,
    )
