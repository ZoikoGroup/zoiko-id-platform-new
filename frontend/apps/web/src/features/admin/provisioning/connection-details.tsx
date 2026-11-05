"use client";

import { Code, CopyId, KeyValues } from "@zoiko/ui";
import type { ReactNode } from "react";

export const SCIM_BASE_PATH = "/scim/v2";
export const LIFECYCLE_PATH = "/provisioning/lifecycle-events";

type Row = [string, ReactNode];

/** How a SCIM client reaches its source. After registering, the cell gives the full URLs; the list view only knows the path. */
export function ConnectionDetails({
  sourceId,
  clientId,
  scimBaseUrl,
  tokenEndpoint,
  clientCreated,
}: {
  sourceId?: string;
  clientId?: string;
  scimBaseUrl?: string;
  tokenEndpoint?: string;
  clientCreated?: boolean;
}) {
  const rows: Row[] = [
    ["SCIM base URL", <CopyId key="b" value={scimBaseUrl ?? SCIM_BASE_PATH} short={false} label="SCIM base URL" />],
    ...(tokenEndpoint ? ([["Token endpoint", <CopyId key="t" value={tokenEndpoint} short={false} label="token endpoint" />]] as Row[]) : []),
    ...(clientId ? ([[clientCreated ? "Client ID (created for you)" : "OAuth client", <CopyId key="c" value={clientId} short={false} label="client ID" />]] as Row[]) : []),
    ...(sourceId ? ([["Source", <CopyId key="s" value={sourceId} label="source ID" />]] as Row[]) : []),
    ["HR lifecycle events", <CopyId key="l" value={LIFECYCLE_PATH} short={false} label="lifecycle events path" />],
    ["Grant / scope", <span key="g" className="text-xs">client_credentials · <Code>scim:provision</Code></span>],
    ["Client authentication", <span key="a" className="text-xs"><Code>private_key_jwt</Code> (signed with the private key that matches the public keys on file)</span>],
  ];
  return (
    <div className="space-y-3">
      <KeyValues items={rows} />
      <p className="text-xs text-ink-subtle">
        {scimBaseUrl ? null : (
          <>
            The base URL is on the cell API host (the same origin that serves <Code>/oauth2/token</Code>), for example{" "}
            <Code>https://&lt;cell-api-host&gt;{SCIM_BASE_PATH}</Code>.{" "}
          </>
        )}
        The SCIM client asks the token endpoint for a token with its client ID and a signed assertion; this page issues no SCIM token and holds no secret.
        It sees and changes only the users and groups of this source.
      </p>
    </div>
  );
}
