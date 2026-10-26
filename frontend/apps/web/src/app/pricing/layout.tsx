import { ZoikoMark } from "@zoiko/ui";
import Link from "next/link";
import type { ReactNode } from "react";

/** Public pricing surface: no session, no tenant data. */
export default function PricingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between px-6 py-5">
        <span className="flex items-center gap-2">
          <ZoikoMark />
          <span className="text-sm font-bold tracking-tight text-ink">
            ZOIKO <span className="text-brand-600">iD</span>
          </span>
        </span>
        <Link href="/sign-in" className="text-sm font-medium text-brand-700 underline">
          Sign in
        </Link>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pb-16 pt-6 sm:px-6">
        {children}
      </main>
    </div>
  );
}
