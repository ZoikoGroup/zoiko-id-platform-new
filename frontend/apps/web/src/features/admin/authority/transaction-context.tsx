"use client";

import { Code, Switch, TextField } from "@zoiko/ui";
import { useState } from "react";

/** The canonical string an approval is bound to; must match `protected.transaction_context`. */
export function buildTransactionContext(action: string, resource: string, change: string) {
  return `${action.trim()}|${resource.trim()}|${change}`;
}

/**
 * Enter a transaction context either from its parts (action, resource, change) or as the exact
 * string. The cell stores only a hash, so the same string must be supplied again to decide or read.
 */
export function TransactionContextField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [raw, setRaw] = useState(false);
  const [parts, setParts] = useState({ action: "", resource: "", change: "" });
  const update = (k: keyof typeof parts) => (v: string) => {
    const next = { ...parts, [k]: v };
    setParts(next);
    onChange(next.action.trim() || next.resource.trim() || next.change ? buildTransactionContext(next.action, next.resource, next.change) : "");
  };

  return (
    <fieldset className="space-y-3 rounded-lg border border-border p-4">
      <legend className="px-1 text-sm font-semibold text-ink">Transaction context</legend>
      <p className="text-xs text-ink-muted">
        An approval is bound to one exact transaction: <Code>action|resource|change</Code>, for example{" "}
        <Code>{"authentication.recovery.policy.manage|principal:<id>|<change>"}</Code>. Use the catalogued action name, the resource as{" "}
        <Code>type:id</Code>, and the exact change the protected action reports (often empty). If an action was refused with
        &quot;Approval required&quot;, use the same action, resource and change. The cell keeps only a hash, so keep this string: you need it to decide on or look up the
        request.
      </p>
      <Switch
        isSelected={raw}
        onChange={(v) => {
          setRaw(v);
          if (!v) onChange(parts.action.trim() || parts.resource.trim() || parts.change ? buildTransactionContext(parts.action, parts.resource, parts.change) : "");
        }}
      >
        Enter the exact string
      </Switch>
      {raw ? (
        <TextField label="Transaction context" isRequired multiline rows={2} mono value={value} onChange={onChange} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <TextField label="Action" isRequired mono value={parts.action} onChange={update("action")} placeholder="credential.trust.manage" />
            <TextField label="Resource" isRequired mono value={parts.resource} onChange={update("resource")} placeholder="credential_issuer:*" />
            <TextField label="Change" mono value={parts.change} onChange={update("change")} description="Optional." />
          </div>
          <p className="text-xs text-ink-muted">
            Result: <Code>{value || "—"}</Code>
          </p>
        </>
      )}
    </fieldset>
  );
}
