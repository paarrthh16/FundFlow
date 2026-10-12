from django.db import models


class LineItemStatus(models.TextChoices):
    OPEN = "open", "Open"
    PARTIALLY_FUNDED = "partially_funded", "Partially funded"
    FUNDED = "funded", "Funded"


class TransactionType(models.TextChoices):
    CREDIT = "credit", "Credit"
    DEBIT = "debit", "Debit"


class TransactionStatus(models.TextChoices):
    SUGGESTED = "suggested", "Suggested"
    NEEDS_REVIEW = "needs_review", "Needs review"
    RECONCILED = "reconciled", "Reconciled"
    IGNORED = "ignored", "Ignored"


class MatchStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
