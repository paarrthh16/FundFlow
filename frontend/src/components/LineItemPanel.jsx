import { formatMoney } from "../utils/money.js";
import StatusMessage from "./StatusMessage.jsx";

const STATUS_LABELS = {
  open: "Open",
  partially_funded: "Partially funded",
  funded: "Funded",
};

export default function LineItemPanel({ state, onRetry }) {
  if (state.status === "loading") return <StatusMessage>Loading line items...</StatusMessage>;
  if (state.status === "error") {
    return (
      <StatusMessage kind="error" onRetry={onRetry}>
        Could not load line items: {state.error}
      </StatusMessage>
    );
  }
  if (state.items.length === 0) return <StatusMessage>This organization has no capital-call line items.</StatusMessage>;

  return (
    <div className={state.refreshing ? "table-scroll is-refreshing" : "table-scroll"} aria-busy={Boolean(state.refreshing)}>
      <table>
        <caption className="visually-hidden">Capital-call line items</caption>
        <thead>
          <tr>
            <th scope="col">Capital call</th>
            <th scope="col">Investor</th>
            <th scope="col">Reference</th>
            <th scope="col" className="numeric">Called</th>
            <th scope="col" className="numeric">Funded</th>
            <th scope="col" className="numeric">Remaining</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((item) => (
            <tr key={item.id}>
              <td className="nowrap">
                #{item.capital_call.number} <span className="muted">({item.capital_call.call_date})</span>
              </td>
              <td>{item.investor_name}</td>
              <td className="nowrap">{item.expected_reference}</td>
              <td className="numeric nowrap">{formatMoney(item.amount_called)}</td>
              <td className="numeric nowrap">{formatMoney(item.amount_funded)}</td>
              <td className="numeric nowrap">{formatMoney(item.amount_remaining)}</td>
              <td>
                <span className={`badge badge-${item.status}`}>{STATUS_LABELS[item.status] ?? item.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
