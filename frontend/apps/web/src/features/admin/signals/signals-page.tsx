"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { PublishSignalSection } from "./publish-signal";
import { SecurityStateSection } from "./security-state";
import { StreamsSection } from "./streams";

export function SignalsPage() {
  return (
    <>
      <PageHeader
        title="Security signals"
        description="Shared Signals streams, the security state of principals, sessions and credentials, and governed overrides."
      />
      <Tabs
        label="Security signals"
        tabs={[
          { id: "streams", label: "Streams", content: <StreamsSection /> },
          { id: "state", label: "Security state", content: <SecurityStateSection /> },
          { id: "publish", label: "Publish signal", content: <PublishSignalSection /> },
        ]}
      />
    </>
  );
}
