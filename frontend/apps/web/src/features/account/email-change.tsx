"use client";

import { Banner, Button, Card, PageHeader } from "@zoiko/ui";
import { ApiErrorView, useCellMutation } from "@/lib/cell";

/** The single-use link from "Verify new email address" (ZID-EM-017): the signed-in person confirms the change of their security email (CR-114). */
export function EmailChange({ token }: { token: string | null }) {
  const confirm = useCellMutation((api, t: string) => api.contacts.confirmMine(t), { success: "Your security email was changed" });
  const done = confirm.data?.status === "APPLIED";
  return (
    <>
      <PageHeader title="Confirm your new security email" />
      <Card title="Change of security email" description="Your security notices move to the new address only when you confirm here. The old address is told either way.">
        {!token ? (
          <Banner tone="warn">This link is incomplete. Open the link from the email again, or ask for a new one from Email contacts.</Banner>
        ) : done ? (
          <Banner tone="ok">Security notices now go to {confirm.data?.address}.</Banner>
        ) : (
          <div className="flex flex-col gap-3">
            {confirm.error ? <ApiErrorView error={confirm.error} /> : null}
            <Button onClick={() => confirm.mutate(token)} isDisabled={confirm.isPending}>
              Confirm the new address
            </Button>
          </div>
        )}
      </Card>
    </>
  );
}
