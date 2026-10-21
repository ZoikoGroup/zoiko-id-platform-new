"use client";

import type { AuthorityGrant } from "@zoiko/api-client";
import { useQueries } from "@tanstack/react-query";
import {
  AuthorityPath,
  Badge,
  Banner,
  Button,
  Code,
  CopyId,
  DateTime,
  Divider,
  KeyValues,
  Select,
  SidePanel,
  StatusBadge,
  humanize,
  type AuthorityNode,
} from "@zoiko/ui";
import { ShieldCheck, ShieldX } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";
import { useApi } from "@/lib/runtime";

import { RISK_OPTIONS, ScopeList, shortId } from "./shared";

export function GrantDetail({ grant, onClose, onRevoke }: { grant: AuthorityGrant; onClose: () => void; onRevoke?: () => void }) {
  const id = grant.authority_grant_id;
  // The list row may be stale; the detail always shows the cell's current view.
  const fresh = useCellQuery(["authority", "grants", "one", id], (api) => api.authority.grant(id));
  const g = fresh.data ?? grant;

  return (
    <SidePanel
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Authority grant"
      subtitle={
        <div className="flex flex-wrap items-center gap-1.5">
          <CopyId value={id} label="grant" />
          <StatusBadge state={g.status} kind="Grant" size="xs" />
        </div>
      }
      footer={
        onRevoke ? (
          <Button variant="danger-outline" onPress={onRevoke}>
            Revoke grant
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-5">
        <KeyValues
          items={[
            ["Type", humanize(g.grant_type)],
            ["Continuity", humanize(g.continuity_mode)],
            ["Grantor", <CopyId key="gr" value={g.grantor_principal_id} label="grantor" />],
            ["Grantee", <CopyId key="ge" value={g.grantee_principal_id} label="grantee" />],
            ["On behalf of", <CopyId key="rp" value={g.represented_principal_id} label="represented principal" />],
            ["Parent grant", g.parent_grant_id ? <CopyId key="pg" value={g.parent_grant_id} label="parent grant" /> : "None (top of chain)"],
            ["Valid from", <DateTime key="vf" value={g.valid_from} />],
            ["Valid until", <DateTime key="vt" value={g.valid_to} />],
            ["Value limit", g.value_limit ? <span className="tabular font-mono">{`${g.value_limit.amount} ${g.value_limit.currency}`}</span> : "No value limit"],
            [
              "Delegation",
              g.delegation.allowed ? <Badge tone="info">{`Allowed, depth ≤ ${g.delegation.max_depth}`}</Badge> : "Not allowed",
            ],
            ["Revision", <span key="rev" className="tabular">{g.revision}</span>],
            ["Root assertion", g.authority_root_assertion_id ? <CopyId key="ra" value={g.authority_root_assertion_id} label="root assertion" /> : "—"],
          ]}
        />
        <div className="space-y-3">
          <div>
            <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Actions</h3>
            <div className="mt-1">
              <ScopeList values={g.actions} any="Any action" />
            </div>
          </div>
          <div>
            <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Resources</h3>
            <div className="mt-1">
              <ScopeList values={g.resources} any="Any resource" />
            </div>
          </div>
        </div>
        <Divider />
        <ChainView grantId={id} />
      </div>
    </SidePanel>
  );
}

function ChainView({ grantId }: { grantId: string }) {
  const api = useApi();
  const [risk, setRisk] = useState("1");
  const chain = useCellQuery(["authority", "chain", grantId, risk], (a) => a.authority.chain(grantId, Number(risk)));
  const ids = chain.data?.chain_ids ?? [];
  const links = useQueries({
    queries: ids.map((cid) => ({ queryKey: ["authority", "grants", "one", cid], queryFn: () => api.authority.grant(cid) })),
  });

  return (
    <section aria-labelledby="chain-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="chain-heading" className="text-sm font-semibold text-ink">
            Authority chain
          </h3>
          <p className="text-xs text-ink-muted">Resolved by the cell now, from this grant up to its root.</p>
        </div>
        <Select label="Resolve at risk class" value={risk} onChange={setRisk} options={RISK_OPTIONS} className="w-44" />
      </div>
      <QueryView query={chain} loadingRows={2}>
        {(c) => {
          // chain_ids is leaf first; draw it root → leaf.
          const nodes: AuthorityNode[] = [];
          if (c.authority_root_assertion_id) {
            nodes.push({ id: `root-${c.authority_root_assertion_id}`, kind: "Root assertion", label: shortId(c.authority_root_assertion_id), state: c.valid ? "VALID" : "UNKNOWN" });
          }
          [...c.chain_ids].reverse().forEach((cid) => {
            const g = links[c.chain_ids.indexOf(cid)]?.data;
            nodes.push({
              id: cid,
              kind: cid === grantId ? "This grant" : "Grant",
              label: shortId(cid),
              state: g?.status ?? "UNKNOWN",
              edge: g
                ? {
                    source: humanize(g.grant_type),
                    scope: g.actions === null ? "any action" : `${g.actions.length} action${g.actions.length === 1 ? "" : "s"}`,
                    validUntil: g.valid_to,
                    authorityBearing: true,
                  }
                : undefined,
            });
          });
          return (
            <div className="space-y-3">
              {c.valid ? (
                <Banner tone="ok" icon={ShieldCheck} title={`Valid at R${risk}`}>
                  Every link is active and within its window, and the chain reaches an eligible root.
                </Banner>
              ) : (
                <Banner tone="danger" icon={ShieldX} title={`Not valid at R${risk}`}>
                  This grant cannot currently support actions at this risk class.
                  {c.codes.length ? (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {c.codes.map((code) => (
                        <Code key={code}>{code}</Code>
                      ))}
                    </span>
                  ) : null}
                </Banner>
              )}
              {c.valid && c.codes.length ? (
                <p className="flex flex-wrap items-center gap-1 text-xs text-ink-muted">
                  Notes:{" "}
                  {c.codes.map((code) => (
                    <Code key={code}>{code}</Code>
                  ))}
                </p>
              ) : null}
              {nodes.length ? <AuthorityPath nodes={nodes} /> : <p className="text-sm text-ink-muted">The cell returned no chain links.</p>}
            </div>
          );
        }}
      </QueryView>
    </section>
  );
}
