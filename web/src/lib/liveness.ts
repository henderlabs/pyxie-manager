import type { Liveness } from "@/lib/workloadDetail";

export const LIVENESS_LABEL: Record<Liveness["state"], string> = {
  ok: "OK",
  slow: "Slow",
  unresponsive: "Not responding",
  problem: "Fault",
  stopped: "Stopped",
  unknown: "Unknown",
};

export const LIVENESS_CLASS: Record<Liveness["state"], string> = {
  ok: "bg-good/15 text-good",
  slow: "bg-warn/15 text-warn",
  unresponsive: "bg-bad/15 text-bad",
  problem: "bg-bad/15 text-bad",
  stopped: "bg-muted/15 text-muted",
  unknown: "bg-muted/15 text-muted",
};

/** Sort order: the VMs that need attention first. */
export const LIVENESS_RANK: Record<Liveness["state"], number> = { unresponsive: 0, problem: 1, slow: 2, unknown: 3, ok: 4, stopped: 5 };
