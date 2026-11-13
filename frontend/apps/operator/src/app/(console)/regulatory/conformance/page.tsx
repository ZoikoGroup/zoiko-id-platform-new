import type { Metadata } from "next";

import { ConformancePage } from "@/features/regulatory/conformance";

export const metadata: Metadata = { title: "Conformance" };

export default function Page() {
  return <ConformancePage />;
}
