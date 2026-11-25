import { ZoikoMark } from "@zoiko/ui";
import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  ["/trust", "Overview"],
  ["/trust/certifications", "Assessments"],
  ["/trust/privacy", "Privacy and subprocessors"],
  ["/trust/status", "Service status"],
  ["/trust/advisories", "Advisories"],
  ["/trust/security", "Report a vulnerability"],
] as const;

/** The public Trust Center: no session, nothing about any company. A projection of what the claims register approved. */
export default function TrustLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:p-3">Skip to the content</a>
      <header className="px-6 py-5">
        <span className="flex items-center gap-2">
          <ZoikoMark />
          <span className="text-sm font-bold tracking-tight text-ink">ZOIKO <span className="text-brand-600">iD</span> <span className="font-normal text-ink-muted">Trust Center</span></span>
        </span>
        <nav aria-label="Trust Center" className="mt-4">
          <ul className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            {NAV.map(([href, label]) => <li key={href}><Link href={href} className="text-brand-700 underline">{label}</Link></li>)}
          </ul>
        </nav>
      </header>
      <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 pb-20 pt-4 sm:px-6">{children}</main>
    </div>
  );
}
