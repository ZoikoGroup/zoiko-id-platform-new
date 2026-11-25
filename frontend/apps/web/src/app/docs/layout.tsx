import { ZoikoMark } from "@zoiko/ui";
import type { ReactNode } from "react";

/** Public developer documentation: no session and no sign-in, nothing about any company. */
export default function DocsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center px-6 py-5">
        <span className="flex items-center gap-2">
          <ZoikoMark />
          <span className="text-sm font-bold tracking-tight text-ink">
            ZOIKO <span className="text-brand-600">iD</span> <span className="font-normal text-ink-muted">Developers</span>
          </span>
        </span>
      </header>
      <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 pb-20 pt-4 sm:px-6">
        {children}
      </main>
    </div>
  );
}
