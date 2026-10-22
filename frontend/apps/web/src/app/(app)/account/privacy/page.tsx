import type { Metadata } from "next";

import { PrivacyCenter } from "@/features/account/privacy";

export const metadata: Metadata = { title: "Privacy" };

export default function Page() {
  return <PrivacyCenter />;
}
