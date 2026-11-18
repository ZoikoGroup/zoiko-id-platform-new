import type { Metadata } from "next";

import { EngineReadinessPage } from "@/features/admin/engine-readiness/readiness";

export const metadata: Metadata = { title: "Face engine readiness" };

export default function Page() {
  return <EngineReadinessPage />;
}
