import "@fontsource-variable/inter";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import type { ReactNode } from "react";
import { LocaleProvider, resolveLocale } from "@zoiko/ui";

import { RuntimeProvider } from "@/lib/runtime";
import { server } from "@/server/bff";

export const metadata: Metadata = {
  title: { default: "Zoiko iD", template: "%s · Zoiko iD" },
  description: "Identity, authority and trust administration.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#111827" },
  ],
};

export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: ReactNode }) {
  const { bff } = server();
  const h = await headers();
  const clientId = bff.clientFor(h.get("cookie"));
  // Dates, times and amounts follow the browser's language (UX-31); decided here so the browser renders the same text.
  const locale = resolveLocale(h.get("accept-language"));
  return (
    <html lang={locale} suppressHydrationWarning>
      <body className="min-h-dvh bg-canvas text-ink antialiased">
        <LocaleProvider locale={locale}>
          <RuntimeProvider clientId={clientId}>{children}</RuntimeProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
