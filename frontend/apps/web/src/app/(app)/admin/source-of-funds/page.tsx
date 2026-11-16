import type { Metadata } from "next";

import { SourceOfFundsQueuePage } from "@/features/admin/source-of-funds/queue-page";

export const metadata: Metadata = { title: "Source of funds" };

export default function Page() {
  return <SourceOfFundsQueuePage />;
}
