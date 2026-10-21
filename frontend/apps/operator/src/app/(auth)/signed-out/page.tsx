import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Signed out" };

export default function SignedOut() {
  return (
    <div className="space-y-4 text-center">
      <h1 className="text-2xl font-semibold tracking-tight text-ink">You&apos;re signed out</h1>
      <p className="text-sm text-ink-muted">Your session has ended on this device.</p>
      <Link
        href="/sign-in"
        className="inline-flex h-10 items-center justify-center rounded-control bg-brand-600 px-5 text-sm font-medium text-on-brand hover:bg-brand-700"
      >
        Sign in again
      </Link>
    </div>
  );
}
