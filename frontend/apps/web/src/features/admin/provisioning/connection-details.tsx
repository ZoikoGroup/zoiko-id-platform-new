"use client";

import { Code, CopyId, KeyValues } from "@zoiko/ui";
import type { ReactNode } from "react";

export const SCIM_BASE_PATH = "/scim/v2";

/** How a SCIM client reaches its source. The cell origin isn't exposed to the browser, so only the path is shown. */
export function ConnectionDetails({ sourceId, clientId }: { sourceId?: string; clientId?: string }) {
  return (
    <div className="space-y-3">
      <KeyValues
        items={[
          ["SCIM base URL", <CopyId key="b" value={SCIM_BASE_PATH} short={false} label="SCIM base path" />],
          ...(clientId ? ([["OAuth client", <CopyId key="c" value={clientId} short={false} label="client ID" />]] as [string, ReactNode][]) : []),
          ...(sourceId ? ([["Source", <CopyId key="s" value={sourceId} label="source ID" />]] as [string, ReactNode][]) : []),
          ["Grant / scope", <span key="g" className="text-xs">client_credentials · <Code>scim:provision</Code></span>],
        ]}
      />
      <p className="text-xs text-ink-subtle">
        The base URL is on the cell API host (the same origin that serves <Code>/oauth2/token</Code>), for example{" "}
        <Code>https://&lt;cell-api-host&gt;{SCIM_BASE_PATH}</Code>. The SCIM client authenticates with its own OAuth client credentials;
        this page issues no separate SCIM token. It sees and changes only the users and groups of this source.
      </p>
    </div>
  );
}
