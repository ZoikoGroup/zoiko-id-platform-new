import type { Metadata } from "next";

import { VerificationPage } from "@/features/account/verification";

export const metadata: Metadata = { title: "Identity verification" };

export default function Page() {
  return <VerificationPage />;
}
