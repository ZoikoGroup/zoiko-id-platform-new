"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiSession } from "@zoiko/api-client";
import { Badge, Banner, Button, DataTable, EmptyState, type Column } from "@zoiko/ui";
import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { sessionState } from "./test-lab-logic";

const KEY = ["verify-api", "sessions"];
const when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");

/** The Test Lab (CR-151): make a sandbox verification for this application, open its link as your customer would, and watch it appear in the list with its result. Nothing real is checked. The link is
 * shown once, here, because the cell keeps only a fingerprint of it. */
export function TestLab({ applicationId, active }: { applicationId: string; active: boolean }) {
  const qc = useQueryClient();
  const list = useCellQuery([...KEY, applicationId], (api) => api.verifyApi.sessions(applicationId), { refetchInterval: 8000 });
  const [made, setMade] = useState<VerifyApiSession | null>(null);
  const create = useCellMutation((api) => api.verifyApi.createTestSession(applicationId, {}), {
    onSuccess: (s) => {
      setMade(s);
      void qc.invalidateQueries({ queryKey: [...KEY, applicationId] });
    },
  });
  const columns: Column<VerifyApiSession>[] = [
    { key: "r", header: "Customer reference", pinned: true, render: (s) => <span className="font-mono text-xs">{s.customer_reference}</span> },
    { key: "s", header: "Where it is", render: (s) => <Badge tone={sessionState(s.status).tone}>{sessionState(s.status).label}</Badge> },
    { key: "c", header: "Started", render: (s) => when(s.created_at) },
    { key: "d", header: "Finished", render: (s) => when(s.completed_at) },
  ];
  return (
    <section aria-label="Test Lab" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Test Lab</h3>
        <Button size="sm" icon={<FlaskConical aria-hidden className="size-4" />} isDisabled={!active || create.isPending} pending={create.isPending} onPress={() => create.mutate(undefined)}>
          Make a test verification
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        Try the whole journey without writing code: make a test verification, open its link as your customer would and choose a result, then see it here. It is a test only. Nothing real is checked and no
        personal data is taken.
      </p>
      {made?.launch_url ? (
        <Banner tone="info" title="Open this link as your customer">
          <a className="break-all text-brand-600 underline" href={made.launch_url} target="_blank" rel="noreferrer noopener">
            {made.launch_url}
          </a>{" "}
          It is shown only now and stops working after 30 minutes or when the test is finished.
        </Banner>
      ) : null}
      {create.error ? <ApiErrorView error={create.error} title="The test verification was not made" onRetry={() => create.reset()} /> : null}
      <QueryView query={list}>
        {(page) => (
          <DataTable<VerifyApiSession>
            caption="Test verifications"
            columns={columns}
            rows={page.items}
            rowKey={(s) => s.session_id}
            empty={
              <EmptyState title="No verifications yet" icon={FlaskConical} compact>
                Make a test verification to see how it works.
              </EmptyState>
            }
          />
        )}
      </QueryView>
    </section>
  );
}
