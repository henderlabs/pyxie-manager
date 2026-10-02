const COLOR_GOOD = "#2fbf71";
const COLOR_WARN = "#e5a94c";
const COLOR_BAD = "#e5484d";
const COLOR_MUTED = "#8b95a7";
const COLOR_TRACK = "#232a38";

function colorForPct(value: number | null): string {
  if (value === null) return COLOR_MUTED;
  if (value >= 90) return COLOR_BAD;
  if (value >= 75) return COLOR_WARN;
  return COLOR_GOOD;
}

export function RadialGauge({
  value,
  label,
  size = 96,
  strokeWidth = 10,
  icon,
}: {
  value: number | null;
  label: string;
  size?: number;
  strokeWidth?: number;
  icon?: React.ReactNode;
}) {
  const pct = value === null ? 0 : Math.min(Math.max(value, 0), 100);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct / 100);
  const color = colorForPct(value);

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={COLOR_TRACK} strokeWidth={strokeWidth} />
          {value !== null && (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={color}
              strokeWidth={strokeWidth}
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              strokeLinecap="round"
            />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-lg font-semibold text-text">{value !== null ? `${Math.round(value)}%` : "—"}</span>
        </div>
      </div>
      <div className="text-xs text-muted mt-1.5 text-center flex items-center gap-1.5">
        {icon && <span className="text-proxmox/70">{icon}</span>}
        {label}
      </div>
    </div>
  );
}

export function Meter({ value, width = 88, hostOnly = false }: { value: number | null; width?: number; hostOnly?: boolean }) {
  const pct = value === null ? 0 : Math.min(Math.max(value, 0), 100);
  if (hostOnly && value !== null) {
    // PVE gives no guest memory stats for this VM: the number is the host-side
    // size of its QEMU process (~100%), not usage. Muted, not alarm-coloured.
    return (
      <div
        className="flex items-center gap-2"
        title={`PVE reports only host-side memory for this VM (${Math.round(value)}% of its allocation) -- it provides no guest memory stats, so this is not a real usage reading. Enable the balloon device and guest agent for a true figure.`}
      >
        <div className="h-1.5 rounded-full bg-border overflow-hidden shrink-0" style={{ width }}>
          <div className="h-full rounded-full opacity-40" style={{ width: `${pct}%`, backgroundColor: "#6b7280" }} />
        </div>
        <span className="text-xs text-muted tabular-nums w-9 text-right italic">host</span>
      </div>
    );
  }
  const color = colorForPct(value);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 rounded-full bg-border overflow-hidden shrink-0" style={{ width }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs text-muted tabular-nums w-9 text-right">{value !== null ? `${Math.round(value)}%` : "—"}</span>
    </div>
  );
}
