import type { Workload } from "@/lib/api";

export type WorkloadOverview = {
  workload: Workload;
  node: { id: string; name: string; maintenance_mode: boolean };
  cluster: { id: string; name: string };
  findings: { id: string; severity: string; category: string; title: string }[];
};

export type DiskInfo = { key: string; volume: string; storage: string | null; size: string | null; kind: string; options: Record<string, string>; empty: boolean };
export type NetInfo = { key: string; model: string | null; mac: string | null; bridge: string | null; vlan_tag: string | null; firewall: boolean; rate: string | null; ip: string | null };

export type ConfigSummary = {
  start_at_boot: boolean;
  protection: boolean;
  description: string | null;
  tags: string[];
  lock: string | null;
  ostype: string | null;
  memory_mb: number | null;
  balloon_min_mb: number | null;
  cores: number | null;
  sockets: number;
  vcpus: number | null;
  cpu_type: string | null;
  disks: DiskInfo[];
  nets: NetInfo[];
  // VM-only
  agent_enabled?: boolean;
  bios?: string;
  machine?: string | null;
  scsihw?: string | null;
  boot?: string | null;
  numa?: boolean;
  // container-only
  hostname?: string | null;
  unprivileged?: boolean;
  swap_mb?: number | null;
  features?: string | null;
};

export type LiveStatus = {
  status?: string;
  qmpstatus?: string;
  uptime?: number;
  cpu?: number;
  cpus?: number;
  mem?: number;
  maxmem?: number;
  balloon?: number;
  netin?: number;
  netout?: number;
  diskread?: number;
  diskwrite?: number;
  lock?: string | null;
  "running-qemu"?: string;
  "running-machine"?: string;
  ha?: { managed: boolean; state: string | null; group: string | null };
};

export type LiveCheck = { state: "ok" | "warn" | "bad" | "info" | "unknown"; detail: string };

/** One VM's answer to "is your QEMU responding?" (see api/pyxie_core/vm_liveness.py). */
export type Liveness = { state: "ok" | "slow" | "unresponsive" | "problem" | "stopped" | "unknown"; detail: string; elapsed: number; since?: string | null };

export type WorkloadLive = { status: LiveStatus | null; config: ConfigSummary | null; check: LiveCheck | null; error: string | null };

export type RrdRow = Partial<Record<
  | "time" | "cpu" | "maxcpu" | "mem" | "maxmem" | "memhost" | "netin" | "netout" | "diskread" | "diskwrite"
  | "pressurecpusome" | "pressurecpufull" | "pressureiosome" | "pressureiofull" | "pressurememorysome" | "pressurememoryfull",
  number
>>;

export type Timeframe = "hour" | "day" | "week" | "month" | "year";
export const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: "hour", label: "Hour" },
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

export type WorkloadTask = {
  id: string;
  upid: string;
  task_type: string | null;
  status: string | null;
  exit_status: string | null;
  user: string | null;
  node_id: string | null;
  started_at: string | null;
  ended_at: string | null;
};
