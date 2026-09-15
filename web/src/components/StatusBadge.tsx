const COLOR_MAP: Record<string, string> = {
  healthy: "bg-good/15 text-good",
  good: "bg-good/15 text-good",
  online: "bg-good/15 text-good",
  connected: "bg-good/15 text-good",
  running: "bg-good/15 text-good",
  available: "bg-good/15 text-good",
  valid: "bg-good/15 text-good",
  success: "bg-good/15 text-good",

  warning: "bg-warn/15 text-warn",
  warn: "bg-warn/15 text-warn",
  untested: "bg-warn/15 text-warn",
  unknown: "bg-muted/15 text-muted",
  // A protection result whose provider has been unreachable since its
  // last successful sync -- last-known status is kept, but this flags it
  // as not currently verifiable.
  stale: "bg-warn/15 text-warn",

  critical: "bg-bad/15 text-bad",
  offline: "bg-bad/15 text-bad",
  stopped: "bg-muted/15 text-muted",
  connection_failed: "bg-bad/15 text-bad",
  authentication_failed: "bg-bad/15 text-bad",
  tls_error: "bg-bad/15 text-bad",
  unavailable: "bg-bad/15 text-bad",
  invalid: "bg-bad/15 text-bad",
  failure: "bg-bad/15 text-bad",
  error: "bg-bad/15 text-bad",
};

export default function StatusBadge({ status }: { status: string | null | undefined }) {
  const key = (status || "unknown").toLowerCase();
  const cls = COLOR_MAP[key] || "bg-muted/15 text-muted";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${cls}`}>
      {(status || "unknown").replace(/_/g, " ")}
    </span>
  );
}
