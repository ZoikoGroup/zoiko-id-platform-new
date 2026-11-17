import type { Metadata } from "next";

import { BiometricMethodsPage } from "@/features/regulatory/biometric-methods";

export const metadata: Metadata = { title: "Biometric methods" };

export default function Page() {
  return <BiometricMethodsPage />;
}
