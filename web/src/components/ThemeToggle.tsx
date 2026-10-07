"use client";

import { useEffect, useState } from "react";

type Pref = "light" | "dark" | "system";
const KEY = "pyxie:theme";

function resolve(pref: Pref): "light" | "dark" {
  if (pref === "light" || pref === "dark") return pref;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function apply(pref: Pref) {
  document.documentElement.setAttribute("data-theme", resolve(pref));
}

const OPTIONS: { value: Pref; label: string; icon: React.ReactNode }[] = [
  {
    value: "light",
    label: "Light",
    icon: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>
    ),
  },
  { value: "dark", label: "Dark", icon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /> },
  {
    value: "system",
    label: "Match this computer",
    icon: (
      <>
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </>
    ),
  },
];

/** Light / Dark / Match this computer. Saved per browser; with nothing saved the app stays dark. "Match this computer" follows its setting live. */
export default function ThemeToggle({ bare = false }: { bare?: boolean }) {
  const [pref, setPref] = useState<Pref>("dark");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === "light" || saved === "dark" || saved === "system") setPref(saved);
    } catch {
      // private window: the default (dark) applies
    }
  }, []);

  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  function choose(next: Pref) {
    setPref(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // not saved, still applied for this visit
    }
    apply(next);
  }

  return (
    <div className={`inline-flex rounded overflow-hidden ${bare ? "" : "border border-border"}`} role="radiogroup" aria-label="Colour theme">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={pref === o.value}
          aria-label={o.label}
          title={o.label}
          onClick={() => choose(o.value)}
          className={`px-2 py-1 ${pref === o.value ? "bg-surface2 text-accent" : "text-muted hover:text-text"}`}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {o.icon}
          </svg>
        </button>
      ))}
    </div>
  );
}
