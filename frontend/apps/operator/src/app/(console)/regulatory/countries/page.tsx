import type { Metadata } from "next";

import { CountryOpeningsPage } from "@/features/regulatory/country-openings";

export const metadata: Metadata = { title: "Country features" };

export default function Page() {
  return <CountryOpeningsPage />;
}
