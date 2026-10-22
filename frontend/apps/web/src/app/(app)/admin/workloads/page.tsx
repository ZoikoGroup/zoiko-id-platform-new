import type { Metadata } from "next";

import { WorkloadsPage } from "@/features/admin/workloads/workloads-page";

export const metadata: Metadata = { title: "Workload identity" };

export default function Page() {
  return <WorkloadsPage />;
}
