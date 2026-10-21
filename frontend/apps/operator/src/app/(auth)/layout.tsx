import { EnvironmentBadge, ZoikoMark } from "@zoiko/ui";
import type { ReactNode } from "react";

import { server } from "@/server/bff";

/** Sign-in, consent, recovery and enrollment: a focused, single-column surface. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  const { env } = server();
  const environment = env.environment === "local" ? "development" : env.environment;
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between px-6 py-5">
        <span className="flex items-center gap-2">
          <ZoikoMark />
          <span className="text-sm font-bold tracking-tight text-ink">
            ZOIKO <span className="text-brand-600">iD</span> <span className="font-medium text-ink-muted">Operator Console</span>
          </span>
        </span>
        <EnvironmentBadge environment={environment} />
      </header>
      <main id="main" className="flex flex-1 items-start justify-center px-4 pb-16 pt-6 sm:pt-16">
        <div className="w-full max-w-md">{children}</div>
      </main>
      <footer className="px-6 py-4 text-center text-xs text-ink-subtle">Operator plane · Zero standing privilege · Every action is evidenced</footer>
    </div>
  );
}
