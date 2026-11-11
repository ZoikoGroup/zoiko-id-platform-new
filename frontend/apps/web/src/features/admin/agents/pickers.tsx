"use client";

import type { PrincipalItem, ToolItem } from "@zoiko/api-client";
import { Banner, Checkbox, Select, StatusBadge, humanize, type Option } from "@zoiko/ui";
import Link from "next/link";

import { useCellQuery } from "@/lib/cell";

import { PrincipalField, shortId, useMe } from "../authority/shared";
import { heldActions, missingActions } from "./agents-logic";

/* Pickers for the Agents screens: choose a person, a server or a tool from a list instead of pasting an ID. Each falls back to the
 * plain ID field when the list is not available to the person (the cell still decides every request). */

/** The people of this company, by the name they are known by. Shares its cache with the team pages. */
function useMembers() {
  return useCellQuery(["tenant", "members", "people-lookup"], (api) => api.tenant.members());
}

export function PersonSelect({
  label,
  value,
  onChange,
  description,
  isRequired,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  description?: string;
  isRequired?: boolean;
}) {
  const me = useMe();
  const members = useMembers();
  const people = (members.data?.items ?? []).filter((m) => m.status === "ACTIVE");
  if (members.isError || (members.isSuccess && people.length === 0)) {
    return <PrincipalField label={label} value={value} onChange={onChange} description={description} isRequired={isRequired} preferTypes={["HUMAN"]} />;
  }
  const options: Option[] = people.map((m) => ({
    id: m.principal_id,
    label: m.person.label,
    description: `${humanize(m.role)}${m.principal_id === me ? " · you" : ""}`,
  }));
  // A person who is no longer a member (an older agent's sponsor) stays visible instead of vanishing from the box.
  if (value && !options.some((o) => o.id === value)) options.push({ id: value, label: shortId(value), description: "Not a current member" });
  return (
    <Select
      label={label}
      isRequired={isRequired}
      value={value || null}
      placeholder={members.isPending ? "Loading people…" : "Choose a person…"}
      onChange={onChange}
      options={options}
      description={description}
    />
  );
}

const SERVER_TYPES = new Set<PrincipalItem["principal_type"]>(["APPLICATION", "SERVICE", "WORKLOAD"]);

/** The service or application that hosts a tool (for example an MCP server). */
export function ServerSelect({ value, onChange, description }: { value: string; onChange: (v: string) => void; description?: string }) {
  const principals = useCellQuery(["admin-picker", "principals"], (api) => api.principals.list({ limit: 200 }));
  const servers = (principals.data?.items ?? []).filter((p) => SERVER_TYPES.has(p.principal_type) && p.status === "ACTIVE");
  if (principals.isError || (principals.isSuccess && servers.length === 0)) {
    return <PrincipalField label="Hosting server" value={value} onChange={onChange} description={description} isRequired preferTypes={["APPLICATION", "SERVICE", "WORKLOAD"]} />;
  }
  return (
    <Select
      label="Hosting server"
      isRequired
      value={value || null}
      placeholder={principals.isPending ? "Loading…" : "Choose the service that hosts it…"}
      onChange={onChange}
      options={servers.map((p) => ({ id: p.principal_id, label: `${humanize(p.principal_type)} · ${shortId(p.principal_id)}`, description: p.principal_id }))}
      description={description}
    />
  );
}

/** Tick tools from the registered ones. A draft or suspended tool can be chosen but is marked, since it cannot run yet. */
export function ToolChecklist({
  tools,
  value,
  onChange,
  emptyHint,
}: {
  tools: ToolItem[];
  value: string[];
  onChange: (ids: string[]) => void;
  emptyHint: string;
}) {
  const open = tools.filter((t) => t.status !== "RETIRED");
  if (!open.length) return <p className="rounded-card border border-dashed border-border p-3 text-sm text-ink-muted">{emptyHint}</p>;
  const toggle = (id: string, on: boolean) => onChange(on ? [...new Set([...value, id])] : value.filter((v) => v !== id));
  return (
    <div className="space-y-2 rounded-card border border-border p-3">
      {open.map((t) => (
        <Checkbox key={t.tool_id} isSelected={value.includes(t.tool_id)} onChange={(on) => toggle(t.tool_id, on)}>
          <span className="inline-flex flex-wrap items-center gap-x-2">
            <span className="font-medium">{t.tool_id}</span>
            <code className="rounded bg-sunken px-1 font-mono text-xs text-ink-muted">{t.canonical_action}</code>
            <StatusBadge state={t.status} kind="Tool status" />
          </span>
        </Checkbox>
      ))}
    </div>
  );
}

/**
 * Says, before a person fills a form in, that their grants do not yet allow it, and where to fix that. Without it the first sign
 * is a "Not permitted" after submitting. Shows nothing while it loads, and nothing when the grants already cover the action.
 */
export function AuthorityNotice({ needs, what }: { needs: readonly string[]; what: string }) {
  const me = useMe();
  const grants = useCellQuery(["authority", "grants", "mine-active", me], (api) => api.authority.grants({ status: "ACTIVE", grantee_principal_id: me, limit: 200 }), {
    enabled: Boolean(me),
  });
  if (!grants.data) return null;
  const missing = missingActions(heldActions(grants.data.items), [...needs]);
  if (!missing.length) return null;
  return (
    <Banner tone="warn" title={`Your grants do not yet allow you to ${what}`}>
      <p>
        You hold no grant that includes {missing.map((m, i) => (
          <span key={m}>
            {i ? ", " : ""}
            <code className="font-mono text-xs">{m}</code>
          </span>
        ))}
        , so the cell will refuse this. Under{" "}
        <Link className="font-medium underline" href="/admin/authority">
          Authority & approvals
        </Link>{" "}
        choose <strong>Issue grant</strong> and tick <strong>Agents and tools</strong> (and <strong>Workload identity</strong> for bindings).
      </p>
    </Banner>
  );
}

const INSPECT = "docker inspect --format='{{index .RepoDigests 0}}' your-image";

/**
 * For a person who has never seen a digest: what it is, that it comes from whoever builds the software, and three ways to find it.
 * Shown next to every field that asks for one, so nobody is left to guess what to type.
 */
export function DigestHelp({ canUseTestBuild }: { canUseTestBuild: boolean }) {
  return (
    <details className="rounded-card border border-border bg-canvas p-3 text-sm">
      <summary className="cursor-pointer font-medium text-brand-700">I don&apos;t know what to put here</summary>
      <div className="mt-2 space-y-2 text-ink-muted">
        <p>
          A digest is the fingerprint of one exact build of the software: <code className="font-mono text-xs">sha256:</code> followed by 64 letters and digits. It comes
          from whoever builds the software. It cannot be worked out from a name, so it is not something to make up. A workload gets a token only while the
          software really running matches the approved digest, so a wrong one means no token.
        </p>
        <ul className="list-disc space-y-1 ps-5">
          <li>
            When an image is pushed to a registry, the push prints a line like <code className="font-mono text-xs">digest: sha256:… size: …</code>. Copy the{" "}
            <code className="font-mono text-xs">sha256:…</code> part.
          </li>
          <li>
            On the computer that built it: <code className="font-mono text-xs">docker images --digests</code>, or <code className="break-all font-mono text-xs">{INSPECT}</code>.
          </li>
          <li>
            An image that exists only on that computer, never pushed: its image ID is accepted too:{" "}
            <code className="break-all font-mono text-xs">docker inspect --format=&apos;{"{{.Id}}"}&apos; your-image</code>.
          </li>
          <li>In a registry&apos;s web page (GitHub Packages, Docker Hub, a cloud registry), open the image version and copy its digest.</li>
          <li>
            In <strong>Builds</strong>, type the image name and choose <strong>Look up digest</strong>: the registry says which digest it points at. A build pipeline can
            register builds itself with <code className="font-mono text-xs">tools/scripts/register_build.py</code>.
          </li>
        </ul>
        <p>
          {canUseTestBuild ? (
            <>
              Just trying things out? Choose <strong>A test build</strong> in the list above and nothing needs to be pasted.
            </>
          ) : (
            <>Not sure? Ask the person who builds it to register it under <strong>Builds</strong>, and pick it from the list.</>
          )}
        </p>
      </div>
    </details>
  );
}
