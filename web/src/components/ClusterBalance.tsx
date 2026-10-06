import { COLOR_TRACK, colorForPct } from "@/components/Gauges";
import { HalfGauge } from "@/components/HalfGauge";
import Link from "next/link";

type NodeLoad = {
  id: string;
  name: string;
  status: string;
  cpu_usage_pct: number | null;
  mem_usage_pct: number | null;
  maintenance_mode: boolean;
};

// Balance = 100 minus the gap, in percentage points, between the busiest and the quietest
// node. Memory always counts (it is what limits where a VM can move); CPU only counts once
// the busiest node is past 50%, because a 0%-to-20% CPU spread on an idle cluster is noise.
// Nodes that are offline or in maintenance mode are left out: they are empty on purpose.
export function computeBalance(nodes: NodeLoad[]) {
  const live = nodes.filter((n) => n.status === "online" && !n.maintenance_mode && n.mem_usage_pct !== null);
  if (live.length < 2) return null;
  const mem = live.map((n) => n.mem_usage_pct as number);
  const cpu = live.map((n) => n.cpu_usage_pct).filter((v): v is number => v !== null);
  const memMin = Math.min(...mem);
  const memMax = Math.max(...mem);
  const cpuMin = cpu.length ? Math.min(...cpu) : 0;
  const cpuMax = cpu.length ? Math.max(...cpu) : 0;
  const cpuGap = cpuMax >= 50 ? cpuMax - cpuMin : 0;
  const gap = Math.max(memMax - memMin, cpuGap);
  const score = Math.min(Math.max(100 - gap, 0), 100);
  const word = score >= 80 ? "Even" : score >= 60 ? "Uneven" : "Skewed";
  return {
    score,
    word,
    memMin,
    memMax,
    cpuMin,
    cpuMax,
    memAvg: mem.reduce((a, b) => a + b, 0) / mem.length,
    cpuAvg: cpu.length ? cpu.reduce((a, b) => a + b, 0) / cpu.length : 0,
    counted: live.length,
  };
}

export function BalanceGauge({ nodes, icon }: { nodes: NodeLoad[]; icon?: React.ReactNode }) {
  const b = computeBalance(nodes);
  return (
    <HalfGauge
      value={b ? b.score : null}
      label="Cluster balance"
      icon={icon}
      ticks={[60, 80]}
      scale="balance"
      detail={b ? `${b.word} · memory ${Math.round(b.memMin)}–${Math.round(b.memMax)}%` : null}
      title="100 minus the gap between the busiest and quietest node (memory, plus CPU once a node passes 50%). Nodes in maintenance or offline are left out."
    />
  );
}

// One half-moon per host: memory on the outer arc, CPU on the inner one. The grid wraps, so
// the card keeps working as hosts are added. Hosts in maintenance or offline are dimmed and
// tagged, and (like the balance score) not counted in the cluster averages.
function HostGauge({ n }: { n: NodeLoad }) {
  const w = 132;
  const h = 76;
  const cx = w / 2;
  const cy = 68;
  const out = n.status !== "online" || n.maintenance_mode;
  const mem = n.mem_usage_pct;
  const cpu = n.cpu_usage_pct;
  const arc = (r: number, sw: number, v: number | null) => {
    const d = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
    return (
      <>
        <path d={d} fill="none" stroke={COLOR_TRACK} strokeWidth={sw} strokeLinecap="round" />
        {v !== null && v > 0 && (
          <path d={d} fill="none" stroke={colorForPct(v)} strokeWidth={sw} strokeLinecap="round" pathLength={100} strokeDasharray={`${Math.min(v, 100)} 100`} />
        )}
      </>
    );
  };
  const tag = n.maintenance_mode ? "maintenance" : n.status !== "online" ? n.status : null;
  return (
    <Link
      href={`/infrastructure/nodes/${n.id}`}
      className={`flex flex-col items-center rounded hover:bg-surface2/60 px-1 py-1 ${out ? "opacity-50" : ""}`}
      title={`${n.name}: memory ${mem === null ? "no data" : Math.round(mem) + "%"}, CPU ${cpu === null ? "no data" : Math.round(cpu) + "%"}${tag ? ` (${tag}, not counted in the balance)` : ""}`}
    >
      <div className="relative" style={{ width: w, height: h }}>
        <svg width={w} height={h} aria-hidden="true">
          {arc(56, 8, mem)}
          {arc(42, 8, cpu)}
        </svg>
        <div className="absolute inset-x-0 bottom-0 text-center leading-tight">
          <div className="text-[17px] font-semibold text-text">{mem === null ? "—" : `${Math.round(mem)}%`}</div>
          <div className="text-[11px] text-muted tabular-nums">cpu {cpu === null ? "—" : `${Math.round(cpu)}%`}</div>
        </div>
      </div>
      <div className="text-xs font-medium text-text mt-1">{n.name.replace(/^pve-slc-/, "")}</div>
      {tag && <div className="text-[10px] uppercase tracking-wide text-warn">{tag}</div>}
    </Link>
  );
}

export function NodeBalanceStrip({ nodes }: { nodes: NodeLoad[] }) {
  const b = computeBalance(nodes);
  const sorted = [...nodes].sort((a, c) => a.name.localeCompare(c.name));
  return (
    <div>
      <div className="grid gap-x-2 gap-y-3 justify-items-center" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(128px, 1fr))" }}>
        {sorted.map((n) => (
          <HostGauge key={n.id} n={n} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
        <span>
          outer arc memory · inner arc CPU
          {b && (
            <>
              {" "}
              · cluster avg memory {Math.round(b.memAvg)}%, CPU {Math.round(b.cpuAvg)}%
            </>
          )}
        </span>
        <Link href="/operations/maintenance" className="text-accent hover:underline">
          Balance Load →
        </Link>
      </div>
    </div>
  );
}
