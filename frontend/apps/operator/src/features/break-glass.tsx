"use client";

import { Banner, Button, Card, DataTable, EmptyState, PageHeader } from "@zoiko/ui";
import { Siren } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/op";

import { AccessRequestDialog, GRANTS_KEY, grantColumns } from "./shared";

export function BreakGlassPage() {
  const mine = useCellQuery([...GRANTS_KEY, "mine"], (api) => api.requests("mine"));
  const [open, setOpen] = useState(false);
  return (
    <>
      <PageHeader
        title="Break-glass"
        description="For emergencies only. Scope, duration, separation of duties, your security state, the cell and the tenant's operator jurisdiction rule still apply; approvals and the lockbox do not."
        actions={
          <Button variant="danger" icon={<Siren aria-hidden className="size-4" />} onPress={() => setOpen(true)}>
            Activate break-glass
          </Button>
        }
      />
      <div className="space-y-5">
        <Banner tone="warn" title="Every break-glass is E3 evidence and is independently reviewed">
          Security is alerted the moment you activate it. Data export is never available through break-glass.
        </Banner>
        <Card padded={false} title="My break-glass history">
          <QueryView query={mine}>
            {(d) => (
              <DataTable
                caption="Break-glass grants"
                columns={grantColumns()}
                rows={d.items.filter((g) => g.break_glass)}
                rowKey={(g) => g.jit_grant_id}
                empty={<EmptyState icon={Siren} title="No break-glass use" />}
              />
            )}
          </QueryView>
        </Card>
      </div>
      <AccessRequestDialog isOpen={open} onClose={() => setOpen(false)} breakGlass />
    </>
  );
}
