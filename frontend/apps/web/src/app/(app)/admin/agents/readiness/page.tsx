import type { Metadata } from "next";

import { ReadinessHomePage } from "@/features/admin/agents/readiness/readiness-home";

export const metadata: Metadata = { title: "Access readiness agent" };

export default function Page() {
  return <ReadinessHomePage />;
}
