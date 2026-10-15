// One variant per stage and outcome, labelled with the CONTEXT.md terms. Takeover and Stalled are
// flags on an Application, not stages: render them as Tag.
const LABELS = {
  screening: "Screening",
  qualified: "Qualified",
  approved: "Approved",
  interview_booked: "Interview",
  knockout: "Knockout",
  rejection: "Rejection",
  abandoned: "Abandoned",
  withdrawn: "Withdrawn",
} as const;

export type StatusKind = keyof typeof LABELS;

export function StatusBadge({ status }: { status: StatusKind }) {
  return (
    <span className={`ui-badge ui-badge--${status}`}>
      <span className="ui-badge__dot" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
