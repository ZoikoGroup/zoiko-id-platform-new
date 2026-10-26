"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { AdmSection, NoticesSection, ProcessorsSection } from "./governance";
import { RegistrySection } from "./registry";
import { RequestsSection } from "./requests";
import { RetentionSection } from "./retention";

export function PrivacyPage() {
  return (
    <>
      <PageHeader
        title="Privacy & data rights"
        description="Data-rights requests, the purpose and processing registry with the controller/processor role map, retention and the subject data map. Legal holds are under Evidence & audit."
      />
      <Tabs
        label="Privacy"
        tabs={[
          { id: "requests", label: "Rights requests", content: <RequestsSection /> },
          { id: "registry", label: "Purposes & role map", content: <RegistrySection /> },
          { id: "retention", label: "Retention & data map", content: <RetentionSection /> },
          { id: "processors", label: "Processors", content: <ProcessorsSection /> },
          { id: "notices", label: "Notices", content: <NoticesSection /> },
          { id: "adm", label: "Automated decisions", content: <AdmSection /> },
        ]}
      />
    </>
  );
}
