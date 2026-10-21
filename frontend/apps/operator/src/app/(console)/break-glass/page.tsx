import type { Metadata } from "next";

import { BreakGlassPage } from "@/features/break-glass";

export const metadata: Metadata = { title: "Break-glass" };

export default function Page() {
  return <BreakGlassPage />;
}
