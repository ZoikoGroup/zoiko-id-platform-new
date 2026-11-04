"use client";

import { ApiError } from "@zoiko/api-client";
import { Banner, Button, Select, TextField } from "@zoiko/ui";
import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

const SIMULATOR = "zid-simulator";

/** Is the development provider simulator running in this cell? (404 everywhere but local/test). */
export function useDevSimulator() {
  return useCellQuery(["dev", "verification-simulator"], async (api) => {
    try {
      return await api.dev.simulatorStatus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  });
}

/** No checks configured yet, in a development cell: set up the baseline profile and simulator. */
export function DevSetup() {
  const sim = useDevSimulator();
  const setup = useCellMutation((api) => api.dev.simulatorSetup(), {
    invalidate: [["verification", "profiles"]],
    success: "Development checks ready",
  });
  if (!sim.data) return null;
  return (
    <Banner tone="warn" icon={FlaskConical} title="Development environment">
      This company has no identity check yet. Set up the platform&apos;s standard representative check (IAL-Z2) with the development provider
      simulator.
      <span className="mt-2 flex flex-wrap items-center gap-3">
        <Button size="sm" pending={setup.isPending} onPress={() => setup.mutate(undefined)}>
          Prepare development checks
        </Button>
      </span>
      {setup.error ? <ApiErrorView error={setup.error} /> : null}
    </Banner>
  );
}

/**
 * The applicant's side of the development provider (VERIFY-001 VERIFY-W2). A real provider would
 * take the person to its own document and liveness capture; the simulator only asks for the values
 * its "documents" would show, then answers through the cell's signed provider-callback path.
 */
export function DevProvider({ verificationId, adapterId, onDone }: { verificationId: string; adapterId?: string | null; onDone: () => void }) {
  // Every synthetic company in the test catalogue lists this director, so one pre-filled identity can claim any of them (change it to test a mismatch).
  const [name, setName] = useState("Jane Mary Doe");
  const [birthdate, setBirthdate] = useState("1985-06-15");
  const [outcome, setOutcome] = useState<"VERIFIED" | "FAILED" | "FRAUD">("VERIFIED");
  const complete = useCellMutation(
    (api) => api.dev.simulatorComplete(verificationId, { legal_name: name.trim(), birthdate: birthdate || null, outcome }),
    { success: (r) => `Provider answered: ${r.outcome.toLowerCase()}`, onSuccess: onDone },
  );
  if (adapterId !== SIMULATOR) return null;
  return (
    <div className="space-y-3 rounded-card border border-warn-border bg-warn-bg p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink">
        <FlaskConical aria-hidden className="size-4" /> Development identity provider
      </div>
      <p className="text-xs text-ink-muted">
        Not a real check. Enter what the identity document would show. To become the owner of a simulated company, use a director&apos;s name
        from the register. The form is pre-filled with the director every test company lists (<span className="font-mono">Jane Mary Doe</span>).
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Legal name on the document" isRequired value={name} onChange={setName} />
        <TextField label="Date of birth" type="date" value={birthdate} onChange={setBirthdate} />
      </div>
      <Select
        label="Provider result"
        value={outcome}
        onChange={(v) => setOutcome(v as typeof outcome)}
        options={[
          { id: "VERIFIED", label: "Document and liveness pass" },
          { id: "FAILED", label: "Check fails" },
          { id: "FRAUD", label: "Fraud signal (never auto-passes)" },
        ]}
      />
      <Button pending={complete.isPending} isDisabled={!name.trim()} onPress={() => complete.mutate(undefined)}>
        Complete check
      </Button>
      {complete.error ? <ApiErrorView error={complete.error} /> : null}
    </div>
  );
}
