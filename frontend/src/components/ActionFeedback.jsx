import { useEffect, useRef } from "react";

// Dismissible result banner for approve / reject / ignore (ARCHITECTURE §7.2).
// Success is announced politely; conflicts and failures are announced immediately.
const TITLES = {
  success: "Done",
  conflict: "Already changed",
  error: "Action failed",
};

export default function ActionFeedback({ feedback, onDismiss }) {
  const bannerRef = useRef(null);

  // Move keyboard focus to each *new* result: the button that was used may disappear when the table
  // refreshes. Keyed on the feedback object, so unrelated re-renders never move focus.
  useEffect(() => {
    if (feedback) bannerRef.current?.focus();
  }, [feedback]);

  if (!feedback) return null;
  const urgent = feedback.kind !== "success";
  return (
    <div
      ref={bannerRef}
      tabIndex={-1}
      className={`status status-${feedback.kind} action-feedback`}
      role={urgent ? "alert" : "status"}
    >
      <span>
        <strong>{TITLES[feedback.kind]}:</strong> {feedback.message}
      </span>
      <button type="button" className="link-button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
