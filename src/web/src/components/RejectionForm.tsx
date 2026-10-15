import { useState, type FormEvent } from "react";
import type { RejectionReason } from "../api/types.ts";
import { REJECTION_REASONS, rejectionReasonLabel } from "../lib/labels.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "./ui/Button.tsx";
import "./ui/ui.css";

/** Reason picker for a Rejection. The reason is always required; `other` also needs a note. */
export function RejectionForm(props: {
  submitLabel?: string;
  busy?: boolean;
  onSubmit: (reason: RejectionReason, note: string | null) => void;
  onCancel?: () => void;
}) {
  const [reason, setReason] = useState<RejectionReason | "">("");
  const [note, setNote] = useState("");
  const noteMissing = reason === "other" && note.trim() === "";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!reason || noteMissing) return;
    props.onSubmit(reason, note.trim() || null);
  };

  return (
    <form className="stack" onSubmit={submit} aria-label="Rejection">
      <label className="field">
        Reason
        <select required value={reason} onChange={(e) => setReason(e.target.value as RejectionReason)}>
          <option value="">Choose a reason</option>
          {REJECTION_REASONS.map((r) => (
            <option key={r} value={r}>
              {rejectionReasonLabel[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Note {reason === "other" ? "(required)" : "(optional)"}
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="row">
        <Button variant="danger" type="submit" disabled={!reason || noteMissing || props.busy}>
          {props.submitLabel ?? "Confirm Rejection"}
        </Button>
        {props.onCancel && <Button onClick={props.onCancel}>Cancel</Button>}
      </div>
      <p className="muted">Confirming tells the Candidate with the approved Rejection message.</p>
    </form>
  );
}
