import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "ARCO · Autonomous Relief Corridor Orchestrator",
  description: "Live tactical dashboard — simulated on real event geometry",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="h-full bg-ops-bg text-ops-text antialiased">{children}</body>
    </html>
  );
}
