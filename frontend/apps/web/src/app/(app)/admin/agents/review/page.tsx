import type { Metadata } from "next";

import { ReviewQueuePage } from "@/features/admin/agents/governance/review";

export const metadata: Metadata = { title: "Agent review queue" };

export default function Page() {
  return <ReviewQueuePage />;
}
