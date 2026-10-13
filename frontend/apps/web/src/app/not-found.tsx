import { ZoikoMark } from "@zoiko/ui";
import Link from "next/link";

/** Generic on purpose: never confirms whether a protected object exists. */
export default function NotFound() {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center gap-4 px-4 text-center">
      <ZoikoMark />
      <h1 className="text-xl font-semibold text-ink">This page isn&apos;t available</h1>
      <p className="max-w-sm text-sm text-ink-muted">It may not exist, or your access may not include it.</p>
      <Link href="/account" className="text-sm font-medium text-brand-600 hover:underline">
        Go to my account
      </Link>
    </main>
  );
}
