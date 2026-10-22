import type { Metadata } from "next";

import { DecisionsPage } from "@/features/admin/decisions/decisions-page";

export const metadata: Metadata = { title: "Access decisions" };

export default function Page() {
  return <DecisionsPage />;
}
