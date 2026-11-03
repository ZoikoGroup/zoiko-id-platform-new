import { redirect } from "next/navigation";

/** One decision page: requests open under the organization (WF-013C-03). */
export default async function Page({ params }: { params: Promise<{ caseId: string }> }): Promise<never> {
  redirect(`/organizations/current/approvals/${encodeURIComponent((await params).caseId)}`);
}
