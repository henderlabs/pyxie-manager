export type NodeStatusLive = {
  uptime: number | null;
  cpu_pct: number | null;
  io_delay_pct: number | null;
  loadavg: (number | null)[];
  cpu_model: string | null;
  cpu_threads: number | null;
  cpu_cores: number | null;
  cpu_sockets: number | null;
  mem_used: number | null;
  mem_total: number | null;
  mem_pct: number | null;
  swap_used: number | null;
  swap_total: number | null;
  root_used: number | null;
  root_total: number | null;
  root_pct: number | null;
  kernel: string | null;
  pve_version: string | null;
  boot_mode: string | null;
  secure_boot: boolean | null;
  ksm_shared: number | null;
};

export type NodeStorageRow = {
  name: string;
  type: string | null;
  shared: boolean;
  active: boolean;
  enabled: boolean;
  content: string[];
  used: number | null;
  total: number | null;
  used_pct: number | null;
};

export type NodeLive = { status: NodeStatusLive | null; storage: NodeStorageRow[]; error: string | null };

export type NodeRrdRow = Partial<Record<
  | "time" | "cpu" | "iowait" | "loadavg" | "maxcpu" | "memused" | "memtotal" | "memavailable" | "arcsize"
  | "netin" | "netout" | "swapused" | "swaptotal" | "rootused" | "roottotal"
  | "pressurecpusome" | "pressureiosome" | "pressureiofull" | "pressurememorysome",
  number
>>;
