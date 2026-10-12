import { useEffect, useRef } from "react";

import StatusMessage from "./StatusMessage.jsx";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// Shows the server's import summary exactly as returned (ARCHITECTURE §5 ImportSummary).
export default function ImportSummary({ state }) {
  const resultRef = useRef(null);
  const finished = state.status === "success" || state.status === "error";

  // Focus each *new* finished result (not "uploading", not unrelated re-renders): the upload button
  // the user pressed is cleared/disabled, so keyboard users continue from the result.
  useEffect(() => {
    if (finished) resultRef.current?.focus();
  }, [state, finished]);

  if (state.status === "idle") return null;
  if (state.status === "uploading") return <StatusMessage>Uploading {state.fileName}...</StatusMessage>;
  if (state.status === "error") {
    return (
      <div ref={resultRef} tabIndex={-1} className="import-result">
        <StatusMessage kind="error">
          Import of {state.fileName} failed: {state.error}
        </StatusMessage>
      </div>
    );
  }

  const { imported_count: imported, duplicate_count: duplicateCount, duplicates, errors } = state.summary;
  return (
    <div ref={resultRef} tabIndex={-1} className="import-summary import-result">
      <StatusMessage>
        {state.fileName}: {plural(imported, "transaction")} imported, {plural(duplicateCount, "duplicate")} skipped,{" "}
        {plural(errors.length, "row error")}.
      </StatusMessage>

      {duplicates.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>Duplicate rows (not imported)</caption>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Reference</th>
                <th scope="col">Explanation</th>
              </tr>
            </thead>
            <tbody>
              {duplicates.map((duplicate) => (
                <tr key={`${duplicate.line}-${duplicate.reference}`}>
                  <td className="numeric">{duplicate.line}</td>
                  <td className="nowrap">{duplicate.reference}</td>
                  <td>{duplicate.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {errors.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>Rows with errors (not imported)</caption>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Error</th>
              </tr>
            </thead>
            <tbody>
              {errors.map((error) => (
                <tr key={error.line}>
                  <td className="numeric">{error.line}</td>
                  <td>{error.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
