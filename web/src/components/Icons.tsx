/** Small resource-type icons used next to labels/values throughout the app
 * for quick visual scanning -- no icon library is installed, so these are
 * hand-drawn inline SVGs (same convention as TaskPanel.tsx's PinIcon):
 * 24x24 viewBox, stroke-based, sized via className, colored via currentColor
 * so they inherit whatever text color the caller already uses.
 *
 * Default size is w-4 h-4 (16px) -- bumped up from an initial w-3.5 (14px)
 * pass that read as too small next to the Dashboard's larger numbers;
 * page-header usage sizes up further still (see each page's PageHeader
 * icon= prop, typically w-5 h-5 or w-6 h-6). */

type IconProps = { className?: string };

const common = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function CpuIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="6" y="6" width="12" height="12" rx="1" />
      <path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" />
    </svg>
  );
}

export function MemoryIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="3" y="6" width="18" height="9" rx="1" />
      <path d="M6 15v3M9 15v3M12 15v3M15 15v3M18 15v3" />
    </svg>
  );
}

export function StorageIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </svg>
  );
}

export function ServerIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="3" y="4" width="18" height="6" rx="1" />
      <rect x="3" y="14" width="18" height="6" rx="1" />
      <path d="M7 7h.01M7 17h.01" />
    </svg>
  );
}

export function ClockIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 3" />
    </svg>
  );
}

export function PackageIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M21 8l-9-5-9 5v8l9 5 9-5V8z" />
      <path d="M3 8l9 5 9-5M12 13v8" />
    </svg>
  );
}

export function WorkloadIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="2" y="4" width="20" height="13" rx="1" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}

export function ClusterIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="12" cy="5" r="2.5" />
      <circle cx="5" cy="18" r="2.5" />
      <circle cx="19" cy="18" r="2.5" />
      <path d="M10.5 6.7L6.7 15.8M13.5 6.7l3.8 9.1" />
    </svg>
  );
}

export function DashboardIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="3" y="3" width="8" height="8" rx="1" />
      <rect x="13" y="3" width="8" height="5" rx="1" />
      <rect x="13" y="10" width="8" height="11" rx="1" />
      <rect x="3" y="13" width="8" height="8" rx="1" />
    </svg>
  );
}

export function HealthIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M3 12h4l2-7 4 14 2-7h6" />
    </svg>
  );
}

export function LightbulbIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M9 18h6M10 21h4" />
      <path d="M12 3a6 6 0 0 0-4 10.5c.6.6 1 1.4 1 2.5h6c0-1.1.4-1.9 1-2.5A6 6 0 0 0 12 3z" />
    </svg>
  );
}

export function WrenchIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2-2z" />
    </svg>
  );
}

export function MigrateIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M3 8h13M12 4l4 4-4 4" />
      <path d="M21 16H8M12 20l-4-4 4-4" />
    </svg>
  );
}

export function LinkIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M9 15l6-6" />
      <path d="M11 5.5l1-1a3.5 3.5 0 0 1 5 5l-1 1M13 18.5l-1 1a3.5 3.5 0 0 1-5-5l1-1" />
    </svg>
  );
}

export function ShieldIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

export function GaugeIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M4 15a8 8 0 1 1 16 0" />
      <path d="M12 15l4-5" />
      <path d="M12 15h.01" />
    </svg>
  );
}

export function ChecklistIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />
    </svg>
  );
}

export function MapPinIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M12 21s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  );
}

export function PlugIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-5 5 5 5 0 0 1-5-5V8z" />
      <path d="M12 17v5" />
    </svg>
  );
}

export function KeyIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="8" cy="15" r="4" />
      <path d="M11 12l9-9M17 6l3 3M14 9l2 2" />
    </svg>
  );
}

export function ScrollIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M6 3h12v15a3 3 0 0 1-3 3H6a3 3 0 0 1 3-3h9" />
      <path d="M9 8h6M9 12h6" />
    </svg>
  );
}

export function ReportIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M5 3h10l4 4v14H5z" />
      <path d="M14 3v5h5" />
      <path d="M8 17v-3M12 17v-6M16 17v-4" />
    </svg>
  );
}

export function SlidersIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M4 6h6M14 6h6M4 12h10M18 12h2M4 18h2M10 18h10" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="16" cy="12" r="2" />
      <circle cx="8" cy="18" r="2" />
    </svg>
  );
}

export function GearIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

export function HistoryIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M3 3v6h6" />
      <path d="M3 9a9 9 0 1 1 3 6.7" />
      <path d="M12 8v4l3 2" />
    </svg>
  );
}

export function HourglassIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M6 3h12M6 21h12" />
      <path d="M7 3v3a5 5 0 0 0 10 0V3M7 21v-3a5 5 0 0 1 10 0v3" />
    </svg>
  );
}

export function SpinnerIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  );
}

export function PinIcon({ className = "w-4 h-4", filled = false }: IconProps & { filled?: boolean }) {
  return (
    <svg {...common} className={className} fill={filled ? "currentColor" : "none"}>
      <path d="M12 2 L12 10 M8 10 L16 10 L18 14 L6 14 Z M12 14 L12 22" />
    </svg>
  );
}

export function PlayIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <polygon points="6 3 20 12 6 21 6 3" />
    </svg>
  );
}

export function StopIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="4" y="4" width="16" height="16" rx="1.5" />
    </svg>
  );
}

export function RestartIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
    </svg>
  );
}

export function NetworkIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="12" cy="4" r="2" />
      <circle cx="5" cy="18" r="2" />
      <circle cx="19" cy="18" r="2" />
      <path d="M12 6v5M12 11 6.3 16.3M12 11l5.7 5.3" />
    </svg>
  );
}

export function UsersIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
      <path d="M16 4.5a3 3 0 0 1 0 5.9M21 20c0-2.8-2-5.1-4.7-5.8" />
    </svg>
  );
}

export function BellIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}

export function MailIcon({ className = "w-4 h-4" }: IconProps) {
  return (
    <svg {...common} className={className}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  );
}
