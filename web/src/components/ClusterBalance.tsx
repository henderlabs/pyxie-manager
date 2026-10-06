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

function BarRow({ title, nodes, pick, avg }: { title: string; nodes: NodeLoad[]; pick: (n: NodeLoad) => number | null; avg: number | null }) {
  const H = 56;
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[11px] uppercase tracking-wider text-muted font-semibold">{title}</span>
        {avg !== null && <span className="text-[11px] text-muted tabular-nums">cluster avg {Math.round(avg)}%</span>}
      </div>
      <div className="relative flex items-end justify-between gap-1.5" style={{ height: H + 16 }}>
        {avg !== null && (
          <div
            className="absolute left-0 right-0 border-t border-dashed border-muted/60 pointer-events-none"
            style={{ bottom: 16 + (H * avg) / 100 }}
          />
        )}
        {nodes.map((n) => {
          const v = pick(n);
          const out = n.status !== "online" || n.maintenance_mode;
          return (
            <div
              key={n.id}
              className={`flex-1 flex flex-col items-center ${out ? "opacity-40" : ""}`}
              title={`${n.name}: ${v === null ? "no data" : Math.round(v) + "%"}${out ? (n.maintenance_mode ? " (maintenance, not counted)" : " (offline)") : ""}`}
            >
              <div className="w-full max-w-[26px] rounded-sm overflow-hidden flex items-end" style={{ height: H, background: COLOR_TRACK }}>
                {v !== null && <div className="w-full" style={{ height: `${Math.min(Math.max(v, 0), 100)}%`, background: colorForPct(v) }} />}
              </div>
              <div className="text-[10px] text-muted mt-1 leading-none">{n.name.replace(/^pve-slc-/, "")}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function NodeBalanceStrip({ nodes }: { nodes: NodeLoad[] }) {
  const b = computeBalance(nodes);
  const sorted = [...nodes].sort((a, c) => a.name.localeCompare(c.name));
  return (
    <div>
      <div className="flex flex-col gap-4">
        <BarRow title="Memory by node" nodes={sorted} pick={(n) => n.mem_usage_pct} avg={b ? b.memAvg : null} />
        <BarRow title="CPU by node" nodes={sorted} pick={(n) => n.cpu_usage_pct} avg={b ? b.cpuAvg : null} />
      </div>
      <div className="mt-3 text-right">
        <Link href="/operations/maintenance" className="text-[11px] text-accent hover:underline">
          Balance Load →
        </Link>
      </div>
    </div>
  );
}
