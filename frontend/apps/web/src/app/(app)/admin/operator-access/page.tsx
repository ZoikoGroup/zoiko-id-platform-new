import type { Metadata } from "next";

import { OperatorAccessPage } from "@/features/admin/operator-access/operator-access-page";

export const metadata: Metadata = { title: "Operator access" };

export default function Page() {
  return <OperatorAccessPage />;
}
