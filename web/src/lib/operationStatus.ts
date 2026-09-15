/** Operation status classification, shared by every component that needs
 * to know whether an Operation is done, not-yet-started, or actively
 * running. Every operation TYPE has its own stage vocabulary --
 * node.enter_maintenance goes through "evacuating", workload.resize
 * through "shutting_down" -> "reconfiguring" -> "starting_up",
 * vm.live_migrate through "executing" -> "monitoring" -- so hand-picking
 * an "in-flight" status list per component is fragile by construction:
 * it silently goes stale the moment a caller forgets one operation type's
 * stage names -- a real gap found live: ApplyRightsizingForm's own copy
 * of this list didn't include workload.resize's actual stages, so a poll
 * relying on it thought a resize was done ~19s before it actually was,
 * and the rightsizing table never picked up the real result.
 *
 * Classifying by the small, STABLE terminal/pre-execution sets instead
 * stays correct as new operation types are added, with no per-type list
 * to maintain. Mirrors operations_engine.py's own TERMINAL_STATUSES.
 */
export const TERMINAL_STATUSES = ["completed", "failed", "blocked", "cancelled"];
export const PRE_EXECUTION_STATUSES = ["awaiting_approval", "pending", "dry_run"];

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function isInFlight(status: string): boolean {
  return !TERMINAL_STATUSES.includes(status) && !PRE_EXECUTION_STATUSES.includes(status);
}
