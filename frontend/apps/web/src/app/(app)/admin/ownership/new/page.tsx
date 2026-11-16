import type { Metadata } from "next";

import { NewOwnershipPage } from "@/features/admin/ownership/ownership";

export const metadata: Metadata = { title: "New ownership graph" };

export default function Page() {
  return <NewOwnershipPage />;
}
