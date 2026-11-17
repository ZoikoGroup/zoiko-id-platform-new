import type { Metadata } from "next";

import { VerificationSessionView } from "@/features/account/verification-session";

export const metadata: Metadata = { title: "Verify your identity", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ ref: string }> }) {
  return <VerificationSessionView sessionRef={decodeURIComponent((await params).ref)} />;
}
