# Balance Load, Affinity Rules and automatic balancing

How PyXie keeps a Proxmox cluster even, and how to stay in control of it. Everything here goes through the normal dry-run, approve, execute path
with the same hard checks (affinity rules, memory headroom, CPU compatibility, HA, maintenance mode) and the global write switch.

## Balance Load

Operations > **Balance Load** shows the cluster balance gauge and each node's memory and CPU, then lets you **Preview Balance Load**. Nothing moves until
you approve.

- **Cluster balance** is 100 minus how far node load strays from the cluster average, counting each node in proportion to its memory size (memory always,
  CPU once the busiest node passes 50%). 80 and above is Even, 60 to 79 "A little uneven", below 60 Skewed.
- **Memory by node** shows each node before (grey) and after (coloured) the plan, in percent and GB, and which guests leave and arrive.
- **Planned moves** are the moves in the plan, each checked on its own. Below them, one line summarises everything that was considered but not made:
  **No gain** (it would only trade places or not narrow the gap between two nodes by at least 5 points), **Blocked** (a rule refused it: affinity, headroom,
  CPU compatibility, HA, trust tier) and **Redirected** (the first choice was refused, another node was used). **Show all** opens the full list.
- **Left alone on purpose** lists guests that are pinned to their host, marked Do not move, moved recently, or already moved twice this week.
- You can change any destination in the plan card, set a line to Don't move, or **Re-score** (it re-checks each move's destination only). To re-read the
  whole cluster use **Replace the plan below with a new preview**. While automatic balancing is on, a plan nobody approves for 12 hours is cancelled and replaced at its next run.

### Why did nothing move?

A cluster can read "A little uneven" and still have nothing worth moving: a small node at a low percentage cannot take a large guest without ending fuller
than the node it came from. The refused list says why (for example "it would leave st-pve103 at 82% memory, fuller than st-pve101 is now").

## Affinity Rules

Operations > **Affinity Rules**: keep-apart and keep-together rules for guest pairs or for every guest carrying a tag, with the current status of each
(Holding, Broken, Not met, Nothing to check). Guests are shown as coloured pills that link to the guest; a **hard** rule that is broken is red, a **soft**
preference that is not met is amber. PyXie never moves a guest by itself to fix a rule; it checks the rules on every new move.

## Pinned host and Do not move

On a workload's page, **Pinned host (soft)** keeps balancing from moving the guest away from that host (it can still move toward it, and Maintenance can
still move it). **Do not move** keeps Balance Load and automatic balancing from moving it at all. Both show on the Balance Load page.

## Automatic balancing

The card at the bottom of Balance Load, per cluster:

| Mode | What it does |
| --- | --- |
| Off | Nothing. |
| Recommend only | Prepares a Balance Load plan when the cluster drifts out of balance and waits for you to approve it. |
| Auto-approve | Approves and runs the plan itself, **one move at a time**. Off by default; turning it on needs an explicit confirmation (Aggressive needs a second one), asked again every time. |

| Level | Acts below score | Minimum benefit per move | Moves per plan (recommend) | Gap between plans | A moved guest rests |
| --- | --- | --- | --- | --- | --- |
| Conservative | 60 | 25 | 2 | 6 h | 7 days |
| Moderate | 70 | 15 | 4 | 2 h | 24 h |
| Aggressive | 80 | 10 | 8 | 30 min | 6 h |

You can also choose what to balance (most limited resource, memory, CPU, both), which nodes guests may be taken from, and the days and hours it may
act. It waits when the cluster is paused, outside the window, not quorate, or a plan is already waiting or running.

**How it decides.** It scores the cluster by each node's *busy* level over the last 7 days (the 90th percentile of its load), not by right now, so a server
that is only busy in business hours never looks idle at night. Auto-approve needs 24 hours of history on every node. It never moves a guest back to a host it
left in the last 72 hours, leaves a guest alone once it has moved twice in a week, and ignores moves made by maintenance mode when it counts.

**Auto-approve leaves a plan for you** (the reason is on the card, in the audit log and in the notification) when the write switch is off, a move is not a live
migration or also changes storage, any node would end above 80% memory, or a node is locked by another operation. Conservative also never plans guests whose
downtime tolerance is Low.

**If something goes wrong:** the first failed or blocked automatic move stops it, sends a critical notification and switches the cluster back to Recommend only.

**Stopping it:** **Revoke auto-approve** (red button on the card) switches the cluster back to Recommend only immediately and drops the confirmation; a move
that has not started is cancelled, a migration already running finishes. **Pause for 24 h** and the global write switch (Settings) also stop it.

**Notifications.** Add a rule in Settings > Notifications for the category **Automatic balancing** with minimum severity **Warning** to be emailed when a plan is
ready, auto-approved, or revoked; a stop after a failure is critical. Audit events: `balance.auto_settings_changed`, `balance.auto_plan_created`,
`balance.auto_plan_approved`, `balance.auto_stopped`, `balance.auto_revoked`.

The design notes and the reasoning are in [auto-balance-design.md](auto-balance-design.md).

**Rule colors.** Each affinity rule has a color that paints its line and its guest pills (here and in the "where the guests sit" card). Pick one of the swatches, or any color, when creating or editing a rule; "Default" goes back to the automatic color. (v0.36.6)

**Storage in the plan.** Each plan line shows where the disks are now and where they will land, e.g. `local-lvm → intel-ssd-103`, or `on nas-ds01 (stays)` for a guest on shared storage. It follows the rules: a guest on shared storage stays put; a guest on local storage goes to the destination host's preferred (pinned) storage, else its local pool with the most free space. Changing the destination host updates it. It is informational: to steer it, set the host's default storage on the Nodes page or the guest's storage preference. (v0.36.6)
