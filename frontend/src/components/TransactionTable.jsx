import { formatMoney } from "../utils/money.js";
import StatusMessage from "./StatusMessage.jsx";

const STATUS_LABELS = {
  suggested: "Suggested",
  needs_review: "Needs review",
  reconciled: "Reconciled",
  ignored: "Ignored",
};

// Mirrors the backend rules (services/actions.py): only a credit that is still `suggested` or
// `needs_review` can be acted on, and only its `proposed` matches can be approved or rejected.
const ACTIONABLE_STATUSES = new Set(["suggested", "needs_review"]);
const isActionable = (txn) => txn.type === "credit" && ACTIONABLE_STATUSES.has(txn.status);

const VERBS = {
  approve: { idle: "Approve", pending: "Approving" },
  reject: { idle: "Reject", pending: "Rejecting" },
  ignore: { idle: "Ignore", pending: "Ignoring" },
};

// `pending` maps a transaction id to the action key running for it (e.g. "approve:42"); while any
// action on a transaction is running, all of that transaction's buttons are disabled.
export default function TransactionTable({ state, onRetry, pending = {}, onAction }) {
  if (state.status === "loading") return <StatusMessage>Loading transactions...</StatusMessage>;
  if (state.status === "error") {
    return (
      <StatusMessage kind="error" onRetry={onRetry}>
        Could not load transactions: {state.error}
      </StatusMessage>
    );
  }
  if (state.items.length === 0) return <StatusMessage>No bank transactions have been imported yet.</StatusMessage>;

  return (
    <div className={state.refreshing ? "table-scroll is-refreshing" : "table-scroll"} aria-busy={Boolean(state.refreshing)}>
      <table>
        <caption className="visually-hidden">Bank transactions, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Description</th>
            <th scope="col" className="numeric">Amount</th>
            <th scope="col">Status</th>
            <th scope="col">Suggested investor and reference</th>
            <th scope="col">Reason</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((txn) => (
            <tr key={txn.id}>
              <td className="nowrap">{txn.date}</td>
              <td>{txn.description}</td>
              <td className="numeric nowrap">{formatMoney(txn.amount)}</td>
              <td>
                <span className={`badge badge-${txn.status}`}>{STATUS_LABELS[txn.status] ?? txn.status}</span>
              </td>
              <td>
                {txn.matches.length === 0 ? (
                  <span className="muted">-</span>
                ) : (
                  <ul className="plain-list">
                    {txn.matches.map((match) => (
                      <li key={match.id}>
                        {match.line_item.investor_name} - {match.line_item.expected_reference}{" "}
                        <span className="muted">({match.status})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </td>
              <td>
                {txn.review_reason && <p className="reason">{txn.review_reason}</p>}
                {txn.matches.length > 0 && (
                  <ul className="plain-list">
                    {txn.matches.map((match) => (
                      <li key={match.id} className="reason">
                        {match.reason}
                      </li>
                    ))}
                  </ul>
                )}
                {!txn.review_reason && txn.matches.length === 0 && <span className="muted">-</span>}
              </td>
              <td>
                <TransactionActions
                  txn={txn}
                  pendingKey={pending[txn.id]}
                  refreshing={Boolean(state.refreshing)}
                  onAction={onAction}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TransactionActions({ txn, pendingKey, refreshing, onAction }) {
  if (!onAction || !isActionable(txn)) return <span className="muted">-</span>;
  // While the list is refreshing the rows may be out of date, so actions wait for fresh data.
  const busy = Boolean(pendingKey) || refreshing;
  const proposed = txn.matches.filter((match) => match.status === "proposed");
  // Visible text and accessible name both reflect the pending state of the clicked button.
  const verb = (key) => VERBS[key.split(":")[0]][pendingKey === key ? "pending" : "idle"];
  const label = (key) => (pendingKey === key ? `${verb(key)}...` : verb(key));

  return (
    <div className="actions" aria-busy={busy}>
      {proposed.map((match) => {
        const target = `${match.line_item.expected_reference} (${match.line_item.investor_name})`;
        return (
          <div className="action-group" key={match.id}>
            <span className="action-target">{match.line_item.expected_reference}</span>
            <button
              type="button"
              disabled={busy}
              aria-label={`${verb(`approve:${match.id}`)} match ${target} for transaction ${txn.bank_reference}`}
              onClick={() => onAction("approve", txn, match)}
            >
              {label(`approve:${match.id}`)}
            </button>
            <button
              type="button"
              disabled={busy}
              aria-label={`${verb(`reject:${match.id}`)} match ${target} for transaction ${txn.bank_reference}`}
              onClick={() => onAction("reject", txn, match)}
            >
              {label(`reject:${match.id}`)}
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="secondary"
        disabled={busy}
        aria-label={`${verb(`ignore:${txn.id}`)} transaction ${txn.bank_reference}`}
        onClick={() => onAction("ignore", txn)}
      >
        {label(`ignore:${txn.id}`)}
      </button>
    </div>
  );
}
