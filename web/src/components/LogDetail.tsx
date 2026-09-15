import { Fragment } from "react";

/** Label/value grid for a Table row's expanded detail panel -- the
 * Event Viewer "Details" tab equivalent used by Logging's three tables
 * (Audit Log, PVE Tasks, Internal Jobs). Fields with a null/undefined/""
 * value are skipped rather than shown as "—", since a detail panel that's
 * already opened on purpose should only show what's actually there. */
export function LogDetailGrid({ fields }: { fields: Array<[string, React.ReactNode]> }) {
  const present = fields.filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (present.length === 0) return <p className="text-xs text-muted">Nothing more to show.</p>;
  return (
    <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-xs">
      {present.map(([label, value]) => (
        <Fragment key={label}>
          <dt className="text-muted uppercase tracking-wide">{label}</dt>
          <dd className="text-text font-mono break-all whitespace-pre-wrap">{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** Pretty-printed JSON for a detail field, or null if there's nothing to
 * show (an empty object/array still counts as "nothing"). */
export function prettyJson(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "object" && Object.keys(value as object).length === 0) return null;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
