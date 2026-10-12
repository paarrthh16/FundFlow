import { useRef, useState } from "react";

// File input + upload button (ARCHITECTURE §7.1). Only checks that a file is chosen; CSV parsing and
// validation are Django's job. `onUpload(file)` resolves to true when the server accepted the upload.
export default function CsvUpload({ organizationName, uploading, onUpload }) {
  const [file, setFile] = useState(null);
  const inputRef = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!file || uploading) return;
    let accepted = false;
    try {
      accepted = await onUpload(file);
    } catch {
      // onUpload reports failures itself; an unexpected rejection just keeps the file selected.
      accepted = false;
    }
    if (accepted === true) {
      // Clear the selection so the same file is not re-submitted by accident.
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <form className="upload-form" onSubmit={handleSubmit}>
      <label htmlFor="statement-file">Bank statement CSV for {organizationName}</label>
      <div className="upload-controls">
        <input
          ref={inputRef}
          id="statement-file"
          type="file"
          accept=".csv,text/csv"
          disabled={uploading}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <button type="submit" disabled={!file || uploading}>
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </div>
    </form>
  );
}
