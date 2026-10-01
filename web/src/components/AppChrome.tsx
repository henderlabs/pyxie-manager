"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import TaskPanel, { PANEL_WIDTH_PX } from "@/components/TaskPanel";

export default function AppChrome({ children, version }: { children: React.ReactNode; version: string }) {
  const pathname = usePathname();
  // The Tasks panel is a fixed, always-open dock on every page except the
  // dashboard. On the dashboard it stays out of the way: collapsed edge tab,
  // opened on demand as an overlay.
  const locked = pathname !== "/";
  const [peek, setPeek] = useState(false);

  useEffect(() => {
    setPeek(false); // always start closed when arriving on the dashboard
  }, [pathname]);

  const open = locked || peek;

  return (
    <>
      <Sidebar version={version} />
      <main
        className="flex-1 min-w-0 p-6 transition-[margin-right] duration-200 ease-out"
        style={{ marginRight: locked ? PANEL_WIDTH_PX : 0 }}
      >
        {children}
      </main>
      <TaskPanel open={open} setOpen={setPeek} pinned={locked} setPinned={() => {}} locked={locked} />
    </>
  );
}
