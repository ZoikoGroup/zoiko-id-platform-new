"use client";

import type { ToolItem, ToolSideEffect } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Banner, Button, Card, Checkbox, CopyId, DataTable, Dialog, EmptyState, RiskBadge, Select, StatusBadge, TextField, useToast, type Column } from "@zoiko/ui";
import { Plus, Wrench } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { TOOLS_KEY, UUID_RE } from "./shared";

const EFFECTS: { id: ToolSideEffect; label: string; description: string }[] = [
  { id: "NONE", label: "None", description: "Reads only." },
  { id: "REVERSIBLE", label: "Reversible", description: "Changes something that can be undone." },
  { id: "IRREVERSIBLE", label: "Irreversible", description: "Always needs a mandate." },
  { id: "EXTERNAL_TRANSACTION", label: "External transaction", description: "Moves value or data outside; always needs a mandate." },
];

type Change = "activate" | "suspend" | "retire";

export function ToolsSection() {
  const tools = useCellQuery(TOOLS_KEY, (api) => api.agents.tools());
  const [creating, setCreating] = useState(false);
  const [change, setChange] = useState<{ tool: ToolItem; change: Change } | null>(null);
  const [reason, setReason] = useState("TOOL_COMPROMISED");

  const apply = useCellMutation(
    (api, v: { id: string; change: Change; reason: string }) =>
      v.change === "activate" ? api.agents.activateTool(v.id) : v.change === "suspend" ? api.agents.suspendTool(v.id, v.reason) : api.agents.retireTool(v.id),
    { invalidate: [TOOLS_KEY], success: "Tool updated", onSuccess: () => setChange(null) },
  );
  const close = () => {
    if (apply.isPending) return;
    setChange(null);
    apply.reset();
  };

  const columns: Column<ToolItem>[] = [
    {
      key: "tool",
      header: "Tool",
      pinned: true,
      render: (t) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{t.tool_id}</span>
          <span className="text-2xs text-ink-subtle">v{t.version}</span>
        </span>
      ),
      sortValue: (t) => t.tool_id,
    },
    { key: "status", header: "Status", render: (t) => <StatusBadge state={t.status} kind="Tool status" />, sortValue: (t) => t.status },
    {
      key: "exec",
      header: "Executable",
      render: (t) => (t.executable ? <Badge tone="ok" size="xs">Yes</Badge> : <Badge tone="neutral" size="xs">No: not active</Badge>),
    },
    { key: "action", header: "Authorization action", render: (t) => <span className="font-mono text-xs">{t.canonical_action}</span> },
    { key: "risk", header: "Risk floor", render: (t) => <RiskBadge risk={t.default_risk_class} compact /> },
    { key: "effect", header: "Side effects", render: (t) => t.side_effect.replace("_", " ").toLowerCase() },
    {
      key: "mandate",
      header: "Needs",
      render: (t) => (
        <span className="inline-flex gap-1">
          {t.requires_mandate ? <Badge size="xs" tone="warn">Mandate</Badge> : null}
          {t.requires_human_approval ? <Badge size="xs" tone="warn">Human approval</Badge> : null}
          {!t.requires_mandate && !t.requires_human_approval ? "—" : null}
        </span>
      ),
    },
    { key: "server", header: "Hosted by", render: (t) => <CopyId value={t.server_principal_id} label="server principal" />, defaultHidden: true },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (t) => (
        <span className="inline-flex gap-2">
          {t.status === "DRAFT" || t.status === "SUSPENDED" ? (
            <Button size="xs" onPress={() => setChange({ tool: t, change: "activate" })}>Activate</Button>
          ) : null}
          {t.status === "ACTIVE" ? (
            <Button size="xs" variant="danger-outline" onPress={() => setChange({ tool: t, change: "suspend" })}>Suspend</Button>
          ) : null}
          {t.status !== "RETIRED" ? (
            <Button size="xs" variant="secondary" onPress={() => setChange({ tool: t, change: "retire" })}>Retire</Button>
          ) : null}
        </span>
      ),
    },
  ];

  const verb = change?.change;
  return (
    <>
      <Card
        padded={false}
        title="Tool and capability registry"
        description="A tool must be registered and active before any agent can run it. Appearing in a server's tool list does not make it executable."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register tool
          </Button>
        }
      >
        <QueryView query={tools}>
          {(data) => (
            <DataTable
              caption="Registered tools"
              columns={columns}
              rows={data.items}
              rowKey={(t) => t.tool_id}
              empty={
                <EmptyState icon={Wrench} title="No tools registered">
                  Register each tool an agent may use, with the authorization action it maps to and how risky it is.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <RegisterToolDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Dialog
        isOpen={change !== null}
        onOpenChange={(o) => !o && close()}
        title={verb === "activate" ? "Activate this tool?" : verb === "suspend" ? "Suspend this tool?" : "Retire this tool?"}
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={apply.isPending} onPress={close}>Cancel</Button>
            <Button
              variant={verb === "activate" ? "primary" : "danger"}
              pending={apply.isPending}
              onPress={() => change && apply.mutate({ id: change.tool.tool_id, change: change.change, reason })}
            >
              {verb === "activate" ? "Activate" : verb === "suspend" ? "Suspend now" : "Retire"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p className="font-mono text-xs">{change?.tool.tool_id}</p>
          {verb === "activate" ? <p>Agents that list this tool may then invoke it, subject to their mandate, grants and policy.</p> : null}
          {verb === "suspend" ? <p>Every agent is blocked from invoking this tool immediately. You can activate it again after review.</p> : null}
          {verb === "retire" ? <p>The tool is retired for good. Agents keep it listed but can never run it.</p> : null}
          {verb === "suspend" ? <TextField label="Reason code" mono value={reason} onChange={setReason} /> : null}
          {apply.error ? <ApiErrorView error={apply.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

function RegisterToolDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const [id, setId] = useState("");
  const [version, setVersion] = useState("1");
  const [server, setServer] = useState("");
  const [action, setAction] = useState("");
  const [risk, setRisk] = useState("1");
  const [effect, setEffect] = useState<ToolSideEffect>("NONE");
  const [mandate, setMandate] = useState(false);
  const [human, setHuman] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const forced = effect === "IRREVERSIBLE" || effect === "EXTERNAL_TRANSACTION";

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    if (!/^[a-z0-9][a-z0-9._-]{1,62}$/.test(id.trim())) return setFormError("Tool ID: lowercase letters, digits, dot, dash or underscore.");
    if (!UUID_RE.test(server.trim())) return setFormError("The hosting server must be a registered service or application principal ID.");
    if (!/^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/.test(action.trim())) return setFormError("The authorization action should look like domain.resource.verb.");
    setPending(true);
    try {
      await protect(() =>
        api.agents.registerTool({
          tool_id: id.trim(),
          version: version.trim() || "1",
          server_principal_id: server.trim(),
          canonical_action: action.trim(),
          default_risk_class: Number(risk) as 0 | 1 | 2 | 3,
          side_effect: effect,
          requires_mandate: mandate || forced,
          requires_human_approval: human,
        }),
      );
      await qc.invalidateQueries({ queryKey: TOOLS_KEY });
      toast.notify({ tone: "ok", title: "Tool registered as a draft", body: "Activate it when it has been reviewed." });
      close();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Register tool"
      description="A draft tool cannot be invoked. Activate it after review."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={close}>Cancel</Button>
          <Button pending={pending} onPress={() => void submit()}>Register tool</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Tool ID" isRequired mono value={id} onChange={setId} placeholder="payments.release" />
          <TextField label="Version" mono value={version} onChange={setVersion} />
        </div>
        <TextField label="Hosting server principal ID" isRequired mono value={server} onChange={setServer} description="The registered service or application (for example an MCP server) that hosts the tool." />
        <TextField label="Authorization action" isRequired mono value={action} onChange={setAction} placeholder="payment.release" description="Every call is decided by the policy engine against this action." />
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Risk floor"
            options={[
              { id: "0", label: "R0 Low" },
              { id: "1", label: "R1 Moderate" },
              { id: "2", label: "R2 High" },
              { id: "3", label: "R3 Critical action" },
            ]}
            value={risk}
            onChange={setRisk}
          />
          <Select label="Side effects" options={EFFECTS} value={effect} onChange={(v) => setEffect(v as ToolSideEffect)} />
        </div>
        <div className="flex flex-col gap-2">
          <Checkbox isSelected={mandate || forced} isDisabled={forced} onChange={setMandate}>
            Requires a mandate{forced ? " (always, for this side-effect class)" : ""}
          </Checkbox>
          <Checkbox isSelected={human} onChange={setHuman}>Requires human approval</Checkbox>
        </div>
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
