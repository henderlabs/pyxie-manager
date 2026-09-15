import type { Metadata } from "next";
import fs from "fs";
import path from "path";
import "./globals.css";
import AppChrome from "@/components/AppChrome";

export const metadata: Metadata = {
  title: "PyXie",
  description: "Proxmox VE operations console",
};

// Read directly from the repo-root VERSION file on every request (not baked
// in at build time) so the sidebar always reflects exactly what's on disk --
// same single source of truth the API reads via config.py's _read_version().
function readVersion(): string {
  try {
    return fs.readFileSync(path.join(process.cwd(), "..", "VERSION"), "utf8").trim();
  } catch {
    return "0.0.0-unknown";
  }
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex bg-canvas text-text min-h-screen">
        <AppChrome version={readVersion()}>{children}</AppChrome>
      </body>
    </html>
  );
}
