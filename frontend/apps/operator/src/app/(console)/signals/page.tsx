import type { Metadata } from "next";

import { SignalsPage } from "@/features/signals";

export const metadata: Metadata = { title: "Signals" };

export default function Page() {
  return <SignalsPage />;
}
