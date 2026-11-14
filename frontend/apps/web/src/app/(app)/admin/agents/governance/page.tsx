import type { Metadata } from "next";

import { GovernanceOverview } from "@/features/admin/agents/governance/overview";

export const metadata: Metadata = { title: "Agent governance" };

export default function Page() {
  return <GovernanceOverview />;
}
