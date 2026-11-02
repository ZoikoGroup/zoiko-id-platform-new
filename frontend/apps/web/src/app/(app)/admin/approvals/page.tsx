import { redirect } from "next/navigation";

/** One inbox, not two: the approvals center lives under the organization (WF-013C-02). */
export default function Page(): never {
  redirect("/organizations/current/approvals");
}
