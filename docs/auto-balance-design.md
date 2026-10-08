# Automatic load balancing (DRS-style): design proposal

Status: **proposal, nothing built.** Written 2026-10-08 for discussion; no code changes accompany it. Updated the same day with Phil's first answers (see "Decisions so far").

## Decisions so far (Phil, 2026-10-08)

- **Auto-approve is wanted**, not just recommend-only: available, but with clear warnings and an explicit choice of aggressiveness. Recommend-only still ships first.
- **Do-not-move flag: yes.** A guest (and, as an extension, a tag) can be marked so automatic balancing never moves it.
- **Time window: selectable and customizable** (days and hours, more than one window), not a fixed night-only rule.
- **What the planner balances is selectable: CPU, memory, or both.** The default is the **most limited resource**: whichever of CPU and memory has the least headroom on the busiest node, so the cluster is balanced on the thing that will run out first. Memory still wins ties, because it is what limits where a VM can move.
- **Scope is selectable**: the whole cluster by default, or a chosen set of nodes (and later tags), and per cluster rather than one global switch.

The sections below are updated to match; the remaining open questions are at the end.

## Goal

Balance Load today is a human-driven preview: you open the page, review a plan, approve it. The proposal is a
background mode that keeps the cluster even without someone opening the page, in the spirit of VMware DRS, but
with PyXie's own rule of thumb: **it proposes first, and acts only when you have explicitly allowed it.**

## What already exists (and is reused unchanged)

- The planner (`_placement_recommendations` in `shared/pyxie_core/recommendations.py`): scores every running VM against
  every eligible node, simulates the plan's own earlier moves, and only suggests a move that beats staying put by
  `PLACEMENT_IMPROVEMENT_THRESHOLD` (15 points). It already runs every worker cycle (about 5 minutes) and feeds the
  Recommendations list.
- Per-destination hard blocks, all inside `recommend_destinations`: affinity rules, memory headroom (counting moves
  already planned), CPU compatibility, CRS/HA constraints, PCI passthrough, trust tier, offline and maintenance-mode nodes.
- `cluster.rebalance` (`balance_workflow.py`): packages a plan as one operation with the normal dry-run, approve,
  execute path, node locks, cooperative cancel, and a child `vm.live_migrate` per move that re-validates against live
  state when it runs.
- The Safety Contract gates: the global write kill switch (`app_settings.pve_mutations_enabled`) and the audit trail
  (`write_audit_event`) on every operation.
- The Balance Load page (v0.31.0): cluster balance score, per-node load, before/after memory projection, Passed/Blocked
  explanations. This is the natural home for the settings and history described below.

Nothing in this proposal weakens any of those. Automatic mode is a new *caller* of the same path, not a new path.

## Proposed behavior

### Modes (per cluster, default Off)

1. **Off.** Today's behavior.
2. **Recommend only.** The worker evaluates on its normal cycle; when the cluster is out of balance by more than the
   chosen level allows, it creates (or refreshes) **one** `cluster.rebalance` operation in `awaiting_approval`, tagged
   "automatic", and raises a Health finding / notification ("Cluster is Skewed: a 3-move plan is ready"). A person
   reviews it on the Balance Load page and approves, edits, or dismisses it exactly as a manual plan. Ships first.
3. **Auto-approve (opt-in, second phase).** The same plan is approved by the system, under the extra limits below.
   Enabled per cluster by an admin, behind its own confirmation, and only available when mode 2 has been running
   for a while (see open questions).

### Warnings for auto-approve

Turning on auto-approve shows, before it can be saved: what it will do on its own (live-migrate running VMs without asking),
the selected level's numbers in plain words ("up to 4 moves per run, at most every 2 hours"), the window it may act in, the
guests excluded by the do-not-move flag, and a reminder that the global write kill switch and Pause stop it at any time.
The Aggressive level adds a second confirmation. The card keeps a visible "Automatic: approves on its own" badge while it is on.

### What it balances and where

- **Metric:** Most limited resource (default), Memory, CPU, or Both. "Most limited" picks, per run, the resource with the
  least headroom on the busiest node; the plan is then scored on that one. "Both" requires a move to help neither metric
  worsen past its ceiling.
- **Scope:** the whole cluster (default) or selected nodes, per cluster.
- **Window:** one or more allowed day/time ranges, in the app's timezone; outside them the job only recommends.
- **Do-not-move:** per guest now (the wish list's G1), per tag as a follow-up; also honored by manual Balance Load suggestions.

### Aggressiveness levels

One setting per cluster picks a preset; each number is also individually overridable under "Advanced".

| | Conservative | Moderate | Aggressive |
| --- | --- | --- | --- |
| Acts when cluster balance score is below | 60 (Skewed) | 70 | 80 (Uneven) |
| Minimum benefit per move (score points; the planner's own scale) | 25 | 15 (today) | 10 |
| Max moves per run | 2 | 4 | 8 |
| Max concurrent migrations | 1 | 1 | 2 |
| Cooldown between runs on a cluster | 6 h | 2 h | 30 min |
| Per-guest cooldown after it was moved | 7 days | 24 h | 6 h |
| Guests it may move | running VMs, small first, never `downtime_tolerance=low` | running VMs | running VMs |
| Transport | live only | live only | live only |

Starting values are guesses to be tuned on the lab; the **shape** matters more than the numbers: three knobs
(threshold, minimum benefit, rate) that all err toward doing nothing.

### Hard rules (not tunable)

- Honors affinity rules, memory headroom, CPU compatibility, HA/CRS, PCI passthrough, trust tiers: by construction,
  since it uses the same planner and each child migration re-checks live state when it runs.
- Never touches a node in maintenance mode, a node with an operation in flight (node locks), or a cluster that is not quorate.
- Never moves a guest that is not running, is HA-fenced, is protected by a "do not auto-move" flag (see open questions),
  or whose backup job is currently running (SAFE-BACKUP rules apply as for any migration).
- Live migration only. Anything that would need shutdown/offline transport or a storage change is left in the
  recommendation for a human.
- Honors the global write kill switch: with it off, auto mode may still *recommend*, never *execute*. Flipping it off
  mid-run stops further moves (children already check it per call).
- Stops and alerts on the first failed move; never retries a failed guest in the same run.
- Never makes headroom worse than the guard that exists today: destination must stay under a configurable memory
  ceiling after the move (proposal: 80%).

### Anti-thrash

Balancing every few minutes on noisy numbers causes ping-pong. Mitigations, all cheap:
- Use the **smoothed** node memory over the last hour (the metric history already exists), not the instantaneous number.
- Require the improvement to hold after the plan's own simulation (already true) **and** to still hold when re-evaluated
  at the start of execution.
- Cluster and per-guest cooldowns (table above); a guest moved A to B is not considered for B to A until its cooldown ends.
- A plan that would move a guest back to a node it left in the last 24 h is dropped.

### Audit and visibility

- Every automatic plan is a normal operation: `created_by` is `PyXie (automatic)` (that label already exists), with the
  preset and the numbers that triggered it in the operation context, so "why did this move?" is answerable afterward.
- Balance Load page: a "Automatic balancing" card (mode, level, last run, next eligible run, last result) and an
  "Automatic history" list. The Dashboard balance gauge links there.
- Notifications through the existing rules engine: plan ready (mode 2), plan auto-approved (mode 3), run completed,
  run stopped on failure. Existing flap hold-down applies.
- A one-click **Pause** (and "pause for 24 h") on the card, for maintenance windows and incident work; it is audited too.

### Where it lives

- Worker: one job, after the existing recommendation pass, that reads the cluster's setting, applies the gates, and
  calls the same `dry_run_balance` the page uses (so there is a single planner and a single plan format).
- Settings: new per-cluster policy keys through the existing policies mechanism (`scope_type=cluster`) rather than a
  new table: `balance.mode`, `balance.level`, plus optional overrides. Fits the Balance Load page and the API.
- API: GET/PUT for these settings (admin only, audited); no new write path to PVE.
- Migration: none required beyond policy rows; the demo dataset gets one automatic plan so screenshots show it.

## Phasing

1. **Phase 1, recommend-only** (small): settings + worker job + "automatic" plan creation + card + notification +
   tests (gating, cooldowns, anti-thrash, kill switch). Nothing here moves a VM without a click. Validate on st-pyxie
   for a few weeks and tune the presets against real behavior.
2. **Phase 2, auto-approve** (opt-in): separate admin toggle with its own confirmation text, Conservative level only
   at first, plus the stricter limits above. Needs Phil's explicit go after phase 1 data.
3. **Later**: CPU in the score (the planner is memory-led today), scheduled windows (only balance 22:00-05:00),
   per-tag exclusions, "balance this node only" automation.

## Open questions for Phil

1. **Auto-approve on production.** Decided: wanted, with warnings. Still open: allowed on every cluster, or only ones marked "lab" until trusted?
2. **Do-not-move flag.** Decided: yes, per guest. Open: per tag as well (proposed follow-up)? Also hides the guest from manual suggestions?
3. **Windows.** Decided: customizable. Open: just allowed windows, or also blackout dates (change freezes)?
4. **Level names and numbers.** Are Conservative/Moderate/Aggressive the right words, and are the starting numbers
   (above) sensible to you? A fourth "Custom" preset?
5. **CPU vs memory.** Decided: selectable, default most-limited resource. Open: should the gauge on the Dashboard follow the same choice?
6. **Scope.** Decided: selectable. Open: is per-cluster plus a node selection enough, or do you also want tag-based scope?
7. **Notifications.** Email via the existing rules only, or also an in-app banner on the Dashboard while an automatic
   plan is waiting?
8. **Failure policy.** On a failed automatic move: stop and alert (proposed), or also disable automatic mode until a
   person re-enables it?
9. **Storage moves.** Leave any plan that changes storage to humans (proposed), or allow when the preference is
   already satisfied by shared storage?
