"""
Real concurrent approvals on PostgreSQL (PRD §7.1, SV4–SV7, AC-23, AC-24).

Two threads, each with its own database connection, approve at the same time. The thread that
reaches funding first is paused inside its transaction (holding its row locks) until the main
thread has observed, via pg_stat_activity and pg_blocking_pids(), that the other worker's backend
is blocked by the first worker's backend. This makes the overlap certain rather than probabilistic. Outcomes must be: one success, one 409, funds once.
"""
import threading
import time
from decimal import Decimal
from unittest import mock

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from reconciliation.constants import LineItemStatus, MatchStatus, TransactionStatus
from reconciliation.exceptions import ConflictError
from reconciliation.models import BankTransaction, LineItem, Organization
from reconciliation.services.actions import approve_match
from reconciliation.services.seed import seed_demo

from .helpers import make_match, make_transaction

TIMEOUT = 15


def backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def wait_for_worker_blocked_by_worker(worker_pids, timeout):
    """
    Return (blocked_pid, blocking_pid) once one worker backend is waiting on a lock held by the
    other worker backend; None on timeout. Lock waits by any other backend are ignored.
    """
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            pids = list(worker_pids)
            if len(pids) == 2:
                cursor.execute(
                    "SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity "
                    "WHERE pid = ANY(%s) AND wait_event_type = 'Lock'",
                    [pids],
                )
                for blocked, blockers in cursor.fetchall():
                    other = next(pid for pid in pids if pid != blocked)
                    if other in blockers:
                        return blocked, other
            time.sleep(0.05)
    return None


class ConcurrentApprovalTests(TransactionTestCase):
    def setUp(self):
        seed_demo()
        self.org = Organization.objects.get(slug="northwind")
        self.item = {li.expected_reference: li for li in LineItem.objects.all()}

    def credit(self, reference, amount, *refs):
        status = TransactionStatus.SUGGESTED if len(refs) == 1 else TransactionStatus.NEEDS_REVIEW
        txn = make_transaction(self.org, reference=reference, amount=amount, status=status,
                               review_reason="" if len(refs) == 1 else "Several candidates")
        return txn, [make_match(txn, self.item[ref]) for ref in refs]

    def run_concurrently(self, match_ids):
        """Approve each match id in its own thread; return the list of outcomes."""
        holding_locks, release = threading.Event(), threading.Event()
        results, worker_pids = [], []
        original = LineItem.recompute_status

        def pause_while_holding_locks(item):
            holding_locks.set()
            release.wait(TIMEOUT)
            original(item)

        def worker(match_id):
            try:
                worker_pids.append(backend_pid())  # this thread's own PostgreSQL connection
                approve_match(self.org, match_id)
                results.append("ok")
            except ConflictError as exc:
                results.append(("conflict", exc.detail))
            except Exception as exc:  # surfaced by the assertions below
                results.append(("error", repr(exc)))
            finally:
                connections.close_all()

        with mock.patch.object(LineItem, "recompute_status", autospec=True, side_effect=pause_while_holding_locks):
            threads = [threading.Thread(target=worker, args=(match_id,)) for match_id in match_ids]
            for thread in threads:
                thread.start()
            try:
                self.assertTrue(holding_locks.wait(TIMEOUT), "no approval reached the funding step")
                self.assertIsNotNone(
                    wait_for_worker_blocked_by_worker(worker_pids, TIMEOUT),
                    "second approval never waited on a lock held by the first approval",
                )
            finally:
                release.set()
                for thread in threads:
                    thread.join(TIMEOUT)
        self.assertFalse(any(t.is_alive() for t in threads), "approval threads did not finish")
        return results

    def assertOneSuccessOneConflict(self, results):
        self.assertEqual(len(results), 2, results)
        self.assertEqual(results.count("ok"), 1, results)
        conflicts = [r for r in results if r != "ok"]
        self.assertEqual(conflicts[0][0], "conflict", results)
        return conflicts[0][1]

    def test_concurrent_different_transactions_same_line_item_funds_once(self):
        # CC-1001 has 27,500.00 remaining; two 20,000.00 payments cannot both be approved.
        _, (first,) = self.credit("R1", "20000.00", "CC-1001")
        _, (second,) = self.credit("R2", "20000.00", "CC-1001")

        detail = self.assertOneSuccessOneConflict(self.run_concurrently([first.pk, second.pk]))

        self.assertIn("exceeds the remaining 7,500.00", detail)
        item = LineItem.objects.get(pk=self.item["CC-1001"].pk)
        self.assertEqual((item.amount_funded, item.status), (Decimal("20000.00"), LineItemStatus.PARTIALLY_FUNDED))
        self.assertEqual(
            sorted(BankTransaction.objects.filter(bank_reference__in=["R1", "R2"]).values_list("status", flat=True)),
            sorted([TransactionStatus.RECONCILED, TransactionStatus.SUGGESTED]),
        )

    def test_concurrent_same_match_approval_funds_once(self):
        txn, (match,) = self.credit("R1", "20000.00", "CC-1001")

        self.assertOneSuccessOneConflict(self.run_concurrently([match.pk, match.pk]))

        item = LineItem.objects.get(pk=self.item["CC-1001"].pk)
        self.assertEqual(item.amount_funded, Decimal("20000.00"))
        match.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual((match.status, txn.status), (MatchStatus.APPROVED, TransactionStatus.RECONCILED))

    def test_concurrent_sibling_match_approval_funds_once(self):
        txn, (a, b) = self.credit("R1", "15000.00", "CC-1005", "CC-1006")

        self.assertOneSuccessOneConflict(self.run_concurrently([a.pk, b.pk]))

        funded = LineItem.objects.filter(pk__in=[self.item["CC-1005"].pk, self.item["CC-1006"].pk])
        self.assertEqual(sorted(funded.values_list("amount_funded", flat=True)), [Decimal("0.00"), Decimal("15000.00")])
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(sorted([a.status, b.status]), sorted([MatchStatus.APPROVED, MatchStatus.REJECTED]))


class BlockedWorkerCheckTests(TransactionTestCase):
    """The lock-wait check must identify *our* blocked worker, not any waiting backend."""

    def test_check_ignores_unrelated_lock_waits_and_detects_worker_blocking(self):
        org = Organization.objects.create(slug="acme", name="Acme")
        holder_locked, waiter_ready, release = threading.Event(), threading.Event(), threading.Event()
        pids = {}

        def holder():
            try:
                with transaction.atomic():
                    pids["holder"] = backend_pid()
                    Organization.objects.select_for_update().get(pk=org.pk)
                    holder_locked.set()
                    release.wait(TIMEOUT)
            finally:
                connections.close_all()

        def waiter():
            try:
                pids["waiter"] = backend_pid()
                waiter_ready.set()
                holder_locked.wait(TIMEOUT)
                with transaction.atomic():
                    Organization.objects.select_for_update().get(pk=org.pk)  # blocks on holder
            finally:
                connections.close_all()

        threads = [threading.Thread(target=holder), threading.Thread(target=waiter)]
        for thread in threads:
            thread.start()
        try:
            self.assertTrue(holder_locked.wait(TIMEOUT) and waiter_ready.wait(TIMEOUT))
            # The waiter really is blocked by the holder ...
            self.assertEqual(
                wait_for_worker_blocked_by_worker([pids["holder"], pids["waiter"]], TIMEOUT),
                (pids["waiter"], pids["holder"]),
            )
            # ... but that wait is not reported for an unrelated pair of backends
            # (the old "any backend waiting on a lock" check would have passed here).
            self.assertIsNone(wait_for_worker_blocked_by_worker([pids["holder"], backend_pid()], 1))
        finally:
            release.set()
            for thread in threads:
                thread.join(TIMEOUT)
        self.assertFalse(any(t.is_alive() for t in threads))
