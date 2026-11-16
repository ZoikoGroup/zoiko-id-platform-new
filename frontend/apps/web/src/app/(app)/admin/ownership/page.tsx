import type { Metadata } from "next";

import { OwnershipListPage } from "@/features/admin/ownership/ownership";

export const metadata: Metadata = { title: "Ownership chains" };

export default function Page() {
  return <OwnershipListPage />;
}
