import { ZoikoMark } from "@zoiko/ui";
import type { ReactNode } from "react";

/** Public verifier surface: no session, no sign-in, no tenant data beyond the bounded result a holder chose to share. */
export default function VerifyLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center px-6 py-5">
        <span className="flex items-center gap-2">
          <ZoikoMark />
          <span className="text-sm font-bold tracking-tight text-ink">
            ZOIKO <span className="text-brand-600">iD</span>
          </span>
        </span>
      </header>
      <main id="main" className="mx-auto w-full max-w-2xl flex-1 px-4 pb-16 pt-6 sm:px-6">
        {children}
      </main>
    </div>
  );
}
