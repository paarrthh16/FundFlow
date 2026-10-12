"""
Demo data for `python manage.py seed_demo` (brief §3, PRD §12, decision D-24).

Create-only and idempotent: each record is looked up by its natural key and created only if
missing. Existing rows are never updated, so funding, statuses, bank transactions and matches
survive any number of re-runs. If an existing row differs from the seed values, a warning is
reported and the row is left as it is.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction

from ..models import CapitalCall, InvestorEntity, LineItem, Organization

ORGANIZATIONS = [
    ("northwind", "Northwind Ventures"),
    ("bluebird", "Bluebird Capital"),
]

# (org slug, call number, call date, due date)
CAPITAL_CALLS = [
    ("northwind", 1, date(2026, 3, 25), date(2026, 4, 10)),
    ("northwind", 2, date(2026, 4, 1), date(2026, 4, 20)),
    ("bluebird", 1, date(2026, 4, 1), date(2026, 4, 15)),
]

# (org slug, call number, investor entity, expected reference, amount called)
LINE_ITEMS = [
    ("northwind", 1, "Haller Capital LLC", "CC-1001", Decimal("27500.00")),
    ("northwind", 1, "Rahul Shah Trust", "CC-1002", Decimal("25000.00")),
    ("northwind", 1, "Patel Family Trust", "CC-1003", Decimal("12500.00")),
    ("northwind", 1, "Meridian Partners LP", "CC-1004", Decimal("15000.00")),
    ("northwind", 1, "Oakridge Holdings LLC", "CC-1005", Decimal("15000.00")),
    ("northwind", 2, "Oakridge Holdings LLC", "CC-1006", Decimal("15000.00")),
    ("bluebird", 1, "Cedar Grove Fund LP", "CC-2001", Decimal("40000.00")),
]


@dataclass
class SeedReport:
    created: Counter = field(default_factory=Counter)
    existing: Counter = field(default_factory=Counter)
    warnings: list = field(default_factory=list)


def _get_or_create(report, model, lookup, defaults):
    """Return the row matching `lookup`; create it (validated) only if it does not exist. Never updates."""
    label = model._meta.verbose_name_plural
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        obj = model(**lookup, **defaults)
        obj.full_clean()  # includes the cross-organization check in LineItem.clean()
        obj.save()
        report.created[label] += 1
        return obj

    report.existing[label] += 1
    for name, expected in defaults.items():
        actual = getattr(obj, name)
        if actual != expected:
            report.warnings.append(
                f"{model.__name__} {_lookup_repr(lookup)}: existing {name}={actual!r} differs from seed "
                f"value {expected!r}; left unchanged."
            )
    return obj


def _lookup_repr(lookup):
    return ", ".join(f"{key}={getattr(value, 'pk', value)}" for key, value in lookup.items())


@transaction.atomic
def seed_demo():
    report = SeedReport()

    orgs = {
        slug: _get_or_create(report, Organization, {"slug": slug}, {"name": name})
        for slug, name in ORGANIZATIONS
    }

    calls = {
        (slug, number): _get_or_create(
            report,
            CapitalCall,
            {"organization": orgs[slug], "number": number},
            {"call_date": call_date, "due_date": due_date},
        )
        for slug, number, call_date, due_date in CAPITAL_CALLS
    }

    # One lookup per distinct investor: Oakridge has line items on two calls but is one entity.
    investors = {
        (slug, name): _get_or_create(report, InvestorEntity, {"organization": orgs[slug], "name": name}, {})
        for slug, name in dict.fromkeys((slug, name) for slug, _, name, _, _ in LINE_ITEMS)
    }

    for slug, number, investor_name, reference, amount_called in LINE_ITEMS:
        # Natural key is (capital call, investor), never the expected reference (D-28).
        # Funding fields are deliberately absent from `defaults`, so they are never compared or reset.
        _get_or_create(
            report,
            LineItem,
            {"capital_call": calls[(slug, number)], "investor_entity": investors[(slug, investor_name)]},
            {"organization": orgs[slug], "expected_reference": reference, "amount_called": amount_called},
        )

    return report
