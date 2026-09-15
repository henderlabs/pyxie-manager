import type { Metadata } from "next";
import "./globals.css";
import AppChrome from "@/components/AppChrome";

export const metadata: Metadata = {
  title: "PyXie",
  description: "Proxmox VE operations console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex bg-canvas text-text min-h-screen">
        <AppChrome>{children}</AppChrome>
      </body>
    </html>
  );
}
