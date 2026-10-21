import type { Metadata } from "next";

import { ApprovalsPage } from "@/features/approvals";

export const metadata: Metadata = { title: "Approvals" };

export default function Page() {
  return <ApprovalsPage />;
}
