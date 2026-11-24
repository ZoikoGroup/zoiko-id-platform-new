import type { Metadata } from "next";

import { HostedVerification } from "@/features/verify-hosted/hosted-verification";
import { validToken } from "@/features/verify-hosted/logic";

export const metadata: Metadata = { title: "Verify your identity", robots: { index: false, follow: false }, referrer: "no-referrer" };
export const dynamic = "force-dynamic";

/** A company's verification link, opened by the person it was made for. No sign-in: the link is the grant. */
export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <HostedVerification token={validToken(token) ? token : null} />;
}
