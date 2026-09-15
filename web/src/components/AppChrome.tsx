"use client";

import { useEffect, useState } from "react";
import Sidebar from "@/components/Sidebar";
import TaskPanel, { PANEL_WIDTH_PX } from "@/components/TaskPanel";

const PIN_STORAGE_KEY = "pyxie.taskpanel.pinned";

export default function AppChrome({ children, version }: { children: React.ReactNode; version: string }) {
  const [pinned, setPinned] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(PIN_STORAGE_KEY);
      if (stored === "true") {
        setPinned(true);
        setOpen(true);
      }
    } catch {}
  }, []);

  function setPinnedPersisted(v: boolean) {
    setPinned(v);
    try {
      localStorage.setItem(PIN_STORAGE_KEY, String(v));
    } catch {}
  }

  const reserved = pinned && open;

  return (
    <>
      <Sidebar version={version} />
      <main
        className="flex-1 min-w-0 p-6 transition-[margin-right] duration-200 ease-out"
        style={{ marginRight: reserved ? PANEL_WIDTH_PX : 0 }}
      >
        {children}
      </main>
      <TaskPanel open={open} setOpen={setOpen} pinned={pinned} setPinned={setPinnedPersisted} />
    </>
  );
}
