/** Shown by Next while a page's server data is loading, so a click is visibly acknowledged. */
export default function PageLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-muted" role="status" aria-live="polite">
      <svg className="w-8 h-8 animate-spin text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="9" className="opacity-20" />
        <path d="M12 3a9 9 0 0 1 9 9" />
      </svg>
      <div className="text-sm">{label}</div>
    </div>
  );
}
