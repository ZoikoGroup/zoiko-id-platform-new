import type { Metadata } from "next";

import { OrganizationPage } from "@/features/onboarding/organization-page";
import { carried } from "@/features/onboarding/carry";

export const metadata: Metadata = { title: "Company verification" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  return <OrganizationPage carry={carried(await searchParams)} />;
}
