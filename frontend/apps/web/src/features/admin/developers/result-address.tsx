"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { WebhookEndpoint } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ADDRESS_RULES, addressProblem, isIdentityEvent } from "./result-address-logic";
import { ENDPOINTS_KEY, EVENT_TYPES_KEY } from "./shared";

/** Where we send each result: one address, one button. "Save and test" registers the address, sends it a signed test message and tells you in plain words what it answered. The same idea as a single
 * "decision URL" field in other verification products; the full list of endpoints is still under "More options" for anyone who needs more than one. */
export function ResultAddress() {
  const qc = useQueryClient();
  const eps = useCellQuery(ENDPOINTS_KEY, (api) => api.webhooks.endpoints());
  const types = useCellQuery(EVENT_TYPES_KEY, (api) => api.webhooks.eventTypes());
  const [url, setUrl] = useState("");
  const refresh = () => void qc.invalidateQueries({ queryKey: ENDPOINTS_KEY });

  const saveAndTest = useCellMutation(
    async (api, v: { url: string }) => {
      const events = (types.data?.items ?? []).map((t) => t.type).filter(isIdentityEvent);
      const made = await api.webhooks.createEndpoint({ url: v.url.trim(), event_types: events });
      try {
        return await api.webhooks.verifyEndpoint(made.endpoint_id);
      } finally {
        refresh(); // the address stays saved even if the test failed, so "Test again" works
      }
    },
    { success: "Active. Results will be sent to this address." },
  );
  const testAgain = useCellMutation(
    async (api, e: WebhookEndpoint) => {
      try {
        return await api.webhooks.verifyEndpoint(e.endpoint_id);
      } finally {
        refresh();
      }
    },
    { success: "Active. Results will be sent to this address." },
  );
  const change = useCellMutation((api, e: WebhookEndpoint) => api.webhooks.retireEndpoint(e.endpoint_id), { onSuccess: () => { setUrl(""); saveAndTest.reset(); testAgain.reset(); refresh(); } });

  const problem = addressProblem(url);
  const ready = url.trim() !== "" && problem === null && (types.data?.items ?? []).some((t) => isIdentityEvent(t.type));

  return (
    <div className="space-y-4">
      <QueryView query={eps} loadingRows={2}>
        {(d) => {
          const live = d.items.filter((e) => e.status !== "RETIRED");
          const current = live.find((e) => e.status === "ACTIVE") ?? live[0];
          return (
            <Card title="Where should we send the results?" description="When a check ends, we send its result to this address on your server, signed, so your system can act on it straight away.">
              <div className="space-y-4">
                {current ? (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={current.status === "ACTIVE" ? "ok" : "warn"}>{current.status === "ACTIVE" ? "Active" : "Saved, not tested yet"}</Badge>
                      <code className="break-all font-mono text-sm">{current.url}</code>
                    </div>
                    {current.status === "ACTIVE" ? (
                      <p className="text-sm text-ink-muted">This address answered our test. Every result is now sent here. Check the Delivery log below to see each one arrive.</p>
                    ) : (
                      <Banner tone="warn" title="This address has not passed the test yet">
                        Press <strong>Test again</strong> after you fix it. We send one signed test message and the address must answer 200 with the same challenge token.
                      </Banner>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {current.status !== "ACTIVE" ? (
                        <Button pending={testAgain.isPending} onPress={() => testAgain.mutate(current)}>
                          Test again
                        </Button>
                      ) : null}
                      <Button variant="secondary" pending={change.isPending} onPress={() => change.mutate(current)}>
                        Use a different address
                      </Button>
                    </div>
                    {testAgain.error ? <ApiErrorView error={testAgain.error} title="The test did not pass" onRetry={() => testAgain.reset()} /> : null}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <TextField
                      label="Result address"
                      mono
                      value={url}
                      onChange={setUrl}
                      placeholder="https://your-server.example/zoiko-id/webhook"
                      description="The full address, including the path, exactly as your server serves it."
                      errorMessage={problem ?? undefined}
                    />
                    <Button pending={saveAndTest.isPending} isDisabled={!ready} onPress={() => saveAndTest.mutate({ url })}>
                      Save and test
                    </Button>
                    {saveAndTest.error ? <ApiErrorView error={saveAndTest.error} title="The test did not pass" onRetry={() => saveAndTest.reset()} /> : null}
                  </div>
                )}
              </div>
            </Card>
          );
        }}
      </QueryView>

      <Card title="What the address has to do">
        <ol className="list-decimal space-y-2 pl-5 text-sm text-ink-muted">
          {ADDRESS_RULES.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ol>
        <p className="mt-3 text-sm text-ink-muted">
          No server code yet? You can skip this: ask for each result from your server with the decision call, or watch the Verifications tab. A webhook is what lets a result reach you even when the person never comes back
          to your site.
        </p>
      </Card>
    </div>
  );
}
