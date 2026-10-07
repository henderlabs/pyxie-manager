"use client";

import { Suspense } from "react";
import { usePathname } from "next/navigation";
import NavProgress from "@/components/NavProgress";
import UpdateBanner from "@/components/UpdateBanner";
import Sidebar from "@/components/Sidebar";
import TaskPanel, { PANEL_WIDTH_PX } from "@/components/TaskPanel";

export default function AppChrome({ children, version, instance }: { children: React.ReactNode; version: string; instance: string }) {
  const pathname = usePathname();
  // The Tasks panel is a fixed, always-open dock on every page, the dashboard
  // included (TaskPanel renders nothing on /login).
  const showTasks = pathname !== "/login";

  return (
    <>
      <Suspense fallback={null}>
        <NavProgress />
      </Suspense>
      <Sidebar version={version} instance={instance} />
      <main
        className="flex-1 min-w-0 p-6 transition-[margin-right] duration-200 ease-out"
        style={{ marginRight: showTasks ? PANEL_WIDTH_PX : 0 }}
      >
        <UpdateBanner />
        {children}
      </main>
      {showTasks && <TaskPanel open setOpen={() => {}} pinned setPinned={() => {}} locked />}
    </>
  );
}
