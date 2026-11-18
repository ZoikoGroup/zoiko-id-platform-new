import type { Metadata } from "next";

import { CheckMethodsPage } from "@/features/regulatory/check-methods";

export const metadata: Metadata = { title: "Check methods" };

export default function Page() {
  return <CheckMethodsPage />;
}
