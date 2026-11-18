import type { Metadata } from "next";

import { ScreeningGatesPage } from "@/features/screening/gates";

export const metadata: Metadata = { title: "Release gates" };

export default function Page() {
  return <ScreeningGatesPage />;
}
