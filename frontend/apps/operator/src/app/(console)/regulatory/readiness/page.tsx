import type { Metadata } from "next";

import { ReadinessPage } from "@/features/regulatory/readiness";

export const metadata: Metadata = { title: "Enforce readiness" };

export default function Page() {
  return <ReadinessPage />;
}
