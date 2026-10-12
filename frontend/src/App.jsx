import { useCallback, useEffect, useRef, useState } from "react";

import {
  approveMatch,
  ignoreTransaction,
  importStatement,
  listLineItems,
  listOrganizations,
  listTransactions,
  rejectMatch,
} from "./api/client.js";
import ActionFeedback from "./components/ActionFeedback.jsx";
import CsvUpload from "./components/CsvUpload.jsx";
import ImportSummary from "./components/ImportSummary.jsx";
import LineItemPanel from "./components/LineItemPanel.jsx";
import OrganizationSwitcher from "./components/OrganizationSwitcher.jsx";
import TransactionTable from "./components/TransactionTable.jsx";
import "./App.css";

const LOADING = { status: "loading", items: [], error: null };
const NO_IMPORT = { status: "idle", org: "" };

const isCancellation = (error, signal) => signal?.aborted || error?.name === "AbortError";

export default function App() {
  const [organizations, setOrganizations] = useState(LOADING);
  const [organizationsAttempt, setOrganizationsAttempt] = useState(0);
  const [selectedOrg, setSelectedOrg] = useState("");
  // Each list remembers which organization it was loaded for (ARCHITECTURE §7.2).
  const [transactions, setTransactions] = useState({ ...LOADING, org: "" });
  const [lineItems, setLineItems] = useState({ ...LOADING, org: "" });
  const [dataAttempt, setDataAttempt] = useState(0);
  // The latest upload's state, tagged with the organization it was sent for.
  const [importState, setImportState] = useState(NO_IMPORT);
  // Incremented on every upload and every organization switch: only the upload that matches the
  // current generation may update the UI. An upload is never aborted, because the server may
  // already be processing it; its result is simply discarded if the user has moved on.
  const uploadGeneration = useRef(0);
  // Approve / reject / ignore: running actions keyed by transaction id, and the latest result banner
  // (tagged with its organization). `orgEpoch` changes on every organization switch so that a late
  // action response for a previous organization can neither show feedback nor trigger a refresh.
  const [pendingActions, setPendingActions] = useState({});
  const [actionFeedback, setActionFeedback] = useState(null);
  const orgEpoch = useRef(0);
  const runningTransactions = useRef(new Set());

  // Organizations: tenant-neutral, loaded once (and again on "Try again").
  useEffect(() => {
    const controller = new AbortController();
    setOrganizations(LOADING);
    listOrganizations({ signal: controller.signal })
      .then((items) => {
        if (controller.signal.aborted) return;
        setOrganizations({ status: "ready", items, error: null });
        // Keep a still-valid selection; otherwise default to the first organization.
        setSelectedOrg((current) => (items.some((org) => org.slug === current) ? current : items[0]?.slug ?? ""));
      })
      .catch((error) => {
        if (isCancellation(error, controller.signal)) return;
        setOrganizations({ status: "error", items: [], error: error.message });
      });
    return () => controller.abort();
  }, [organizationsAttempt]);

  // Organization-scoped data. Changing the organization clears both lists immediately, aborts the
  // previous organization's requests, and ignores any response that still arrives for them.
  // A refresh of the *same* organization (after an upload or action) keeps the current rows visible,
  // marked as refreshing, until the replacement data arrives.
  useEffect(() => {
    if (!selectedOrg) return undefined;
    const controller = new AbortController();
    const { signal } = controller;
    const startLoading = (previous) =>
      previous.org === selectedOrg && previous.status === "ready"
        ? { ...previous, refreshing: true }
        : { ...LOADING, org: selectedOrg };
    setTransactions(startLoading);
    setLineItems(startLoading);

    const load = (fetchList, setState) =>
      fetchList(selectedOrg, { signal })
        .then((items) => {
          if (!signal.aborted) setState({ status: "ready", items, error: null, org: selectedOrg, refreshing: false });
        })
        .catch((error) => {
          if (!isCancellation(error, signal)) {
            setState({ status: "error", items: [], error: error.message, org: selectedOrg });
          }
        });

    load(listTransactions, setTransactions);
    load(listLineItems, setLineItems);
    return () => controller.abort();
  }, [selectedOrg, dataAttempt]);

  const retryOrganizations = useCallback(() => setOrganizationsAttempt((n) => n + 1), []);
  const retryData = useCallback(() => setDataAttempt((n) => n + 1), []);

  // Switching organization clears the import summary (PRD §11) and invalidates any upload in flight.
  const selectOrganization = useCallback((slug) => {
    uploadGeneration.current += 1;
    orgEpoch.current += 1;
    runningTransactions.current = new Set();
    setImportState(NO_IMPORT);
    setPendingActions({});
    setActionFeedback(null);
    setSelectedOrg(slug);
  }, []);

  const runAction = useCallback(async (kind, txn, match) => {
    const org = selectedOrg;
    if (!org || runningTransactions.current.has(txn.id)) return; // one action per transaction at a time
    const epoch = orgEpoch.current;
    const running = runningTransactions.current;
    const key = kind === "ignore" ? `ignore:${txn.id}` : `${kind}:${match.id}`;
    const target = match ? `${match.line_item.expected_reference} (${match.line_item.investor_name})` : "";

    running.add(txn.id);
    setPendingActions((current) => ({ ...current, [txn.id]: key }));
    setActionFeedback(null);
    try {
      let message;
      if (kind === "approve") {
        await approveMatch(org, match.id);
        message = `Approved ${target} for ${txn.bank_reference}. The transaction is reconciled and the line item's funding is updated.`;
      } else if (kind === "reject") {
        const result = await rejectMatch(org, match.id);
        const nowNeedsReview = !result.matches.some((m) => m.status === "proposed");
        message = `Rejected ${target} for ${txn.bank_reference}.${nowNeedsReview ? " The transaction now needs review." : ""}`;
      } else {
        await ignoreTransaction(org, txn.id);
        message = `Ignored transaction ${txn.bank_reference}; its proposed matches were rejected.`;
      }
      if (epoch !== orgEpoch.current) return; // organization switched while the request was running
      setActionFeedback({ kind: "success", org, message });
      setDataAttempt((n) => n + 1);
    } catch (error) {
      if (epoch !== orgEpoch.current) return;
      if (error.status === 409) {
        setActionFeedback({
          kind: "conflict",
          org,
          message: `${error.message} The transaction or line item changed since this page was loaded, so the data has been refreshed.`,
        });
        setDataAttempt((n) => n + 1);
      } else {
        setActionFeedback({ kind: "error", org, message: error.message || "The request failed." });
      }
    } finally {
      running.delete(txn.id);
      if (epoch === orgEpoch.current) {
        setPendingActions((current) => {
          const next = { ...current };
          delete next[txn.id];
          return next;
        });
      }
    }
  }, [selectedOrg]);

  const uploadStatement = useCallback(async (file) => {
    const org = selectedOrg;
    if (!org || !file) return false;
    const generation = ++uploadGeneration.current;
    setImportState({ status: "uploading", org, fileName: file.name });
    try {
      const summary = await importStatement(org, file);
      if (generation !== uploadGeneration.current) return false; // organization switched meanwhile
      setImportState({ status: "success", org, fileName: file.name, summary });
      setDataAttempt((n) => n + 1); // reload transactions and line items for this organization
      return true;
    } catch (error) {
      if (generation !== uploadGeneration.current) return false;
      setImportState({ status: "error", org, fileName: file.name, error: error.message });
      return false;
    }
  }, [selectedOrg]);

  // Defence in depth: never render a list that belongs to a different organization.
  const current = (state) => (state.org === selectedOrg ? state : { ...LOADING, org: selectedOrg });
  const currentImport = importState.org === selectedOrg ? importState : NO_IMPORT;
  const currentFeedback = actionFeedback?.org === selectedOrg ? actionFeedback : null;
  const selectedName = organizations.items.find((org) => org.slug === selectedOrg)?.name;

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>FundFlow</h1>
          <p className="muted">Capital-call bank reconciliation</p>
        </div>
        <OrganizationSwitcher
          organizations={organizations}
          selected={selectedOrg}
          onSelect={selectOrganization}
          onRetry={retryOrganizations}
        />
      </header>

      {selectedOrg && (
        <main aria-label={selectedName ? `${selectedName} reconciliation data` : undefined}>
          <section className="panel" aria-labelledby="import-heading">
            <h2 id="import-heading">Import bank statement</h2>
            {/* key: a new organization starts with an empty file selection */}
            <CsvUpload
              key={selectedOrg}
              organizationName={selectedName ?? selectedOrg}
              uploading={currentImport.status === "uploading"}
              onUpload={uploadStatement}
            />
            <ImportSummary state={currentImport} />
          </section>

          <section className="panel" aria-labelledby="transactions-heading">
            <h2 id="transactions-heading">Bank transactions</h2>
            <ActionFeedback feedback={currentFeedback} onDismiss={() => setActionFeedback(null)} />
            <TransactionTable
              state={current(transactions)}
              onRetry={retryData}
              pending={pendingActions}
              onAction={runAction}
            />
          </section>

          <section className="panel" aria-labelledby="line-items-heading">
            <h2 id="line-items-heading">Capital-call line items</h2>
            <LineItemPanel state={current(lineItems)} onRetry={retryData} />
          </section>
        </main>
      )}
    </div>
  );
}
