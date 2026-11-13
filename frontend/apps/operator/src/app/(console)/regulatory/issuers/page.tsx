import type { Metadata } from "next";

import { IssuersPage } from "@/features/regulatory/issuers";

export const metadata: Metadata = { title: "Issuers" };

export default function Page() {
  return <IssuersPage />;
}
