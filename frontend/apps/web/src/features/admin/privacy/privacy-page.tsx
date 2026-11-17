"use client";

import { PageHeader, Tabs } from "@zoiko/ui";

import { BiometricSection } from "./biometric";
import { BreachesSection } from "./breaches";
import { DpiaSection } from "./dpia";
import { ErasureSection } from "./erasure";
import { AdmSection, NoticesSection, ProcessorsSection } from "./governance";
import { RegistrySection } from "./registry";
import { RequestsSection } from "./requests";
import { RetentionSection } from "./retention";

export function PrivacyPage() {
  return (
    <>
      <PageHeader
        title="Privacy & data rights"
        description="Data-rights requests, the purpose and processing registry with the controller/processor role map, retention and the subject data map, DPIA packages and their release gates, breach cases, and whether biometric methods may run. Legal holds are under Evidence & audit."
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
          { id: "dpia", label: "DPIA", content: <DpiaSection /> },
          { id: "breaches", label: "Breaches", content: <BreachesSection /> },
          { id: "erasure", label: "Erasure", content: <ErasureSection /> },
          { id: "biometric", label: "Biometric readiness", content: <BiometricSection /> },
        ]}
      />
    </>
  );
}
