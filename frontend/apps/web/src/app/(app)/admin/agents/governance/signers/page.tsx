import type { Metadata } from "next";

import { SignersPage } from "@/features/admin/agents/governance/signers";

export const metadata: Metadata = { title: "Agent approval signers" };

export default function Page() {
  return <SignersPage />;
}
