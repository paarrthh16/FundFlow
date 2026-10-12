// Shared loading / empty / error display (ARCHITECTURE §7.1).
// Loading and empty messages are polite live regions; errors are announced immediately.
export default function StatusMessage({ kind = "info", children, onRetry }) {
  const isError = kind === "error";
  return (
    <div className={`status status-${kind}`} role={isError ? "alert" : "status"}>
      <span>{children}</span>
      {isError && onRetry && (
        <button type="button" className="link-button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
