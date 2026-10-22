import "@fontsource-variable/inter";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { RuntimeProvider } from "@/lib/runtime";
import { server } from "@/server/bff";

export const metadata: Metadata = {
  title: { default: "Zoiko iD Operator Console", template: "%s · Operator Console" },
  description: "Zoiko iD operator plane: just-in-time access, break-glass and post-review.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#111827" },
  ],
};

export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: ReactNode }) {
  const { env } = server();
  return (
    <html lang="en-US" suppressHydrationWarning>
      <body className="min-h-dvh bg-canvas text-ink antialiased">
        <RuntimeProvider clientId={env.clientId}>{children}</RuntimeProvider>
      </body>
    </html>
  );
}
