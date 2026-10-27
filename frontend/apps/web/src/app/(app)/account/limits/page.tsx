import type { Metadata } from "next";

import { MyLimitsPage } from "@/features/account/my-limits";

export const metadata: Metadata = { title: "My limits" };

export default function Page() {
  return <MyLimitsPage />;
}
