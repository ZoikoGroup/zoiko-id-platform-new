import type { Metadata } from "next";

import { ClaimsPage } from "@/features/regulatory/claims";

export const metadata: Metadata = { title: "Regulated claims" };

export default function Page() {
  return <ClaimsPage />;
}
