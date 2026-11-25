import type { VerifyApiApplication } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { draftProblem, toDraft, toSettings, workflowLabel } from "./settings-logic";

const app = (over: Partial<VerifyApiApplication> = {}): VerifyApiApplication => ({
  application_id: "a1",
  name: "Checkout",
  environment: "SANDBOX",
  status: "ACTIVE",
  scopes: [],
  redirect_origins: ["https://app.example.com"],
  allowed_workflows: [],
  default_workflow_id: null,
  branding: {},
  created_at: "2026-11-24T10:00:00Z",
  updated_at: "2026-11-24T10:00:00Z",
  ...over,
});

describe("application settings form", () => {
  it("starts with every workflow ticked when none is limited", () => {
    const d = toDraft(app());
    expect(d.workflows.sort()).toEqual(["wf_document_only", "wf_document_selfie"]);
    expect(d.origins).toBe("https://app.example.com");
  });
  it("sends every workflow ticked as an empty list, and a limited set as itself", () => {
    expect(toSettings(toDraft(app())).allowed_workflows).toEqual([]);
    const only = { ...toDraft(app()), workflows: ["wf_document_only"] };
    expect(toSettings(only).allowed_workflows).toEqual(["wf_document_only"]);
    expect(toSettings(only).default_workflow_id).toBe("");
  });
  it("sends only the hosted page settings that are filled in", () => {
    const d = { ...toDraft(app()), displayName: " Acme Bank ", theme: "green", supportUrl: "https://acme.example/help" };
    expect(toSettings(d).branding).toEqual({ display_name: "Acme Bank", theme: "green", support_url: "https://acme.example/help" });
    expect(toSettings(toDraft(app())).branding).toEqual({});
  });
  it("names what is wrong, in the order the form is read", () => {
    const base = toDraft(app());
    expect(draftProblem(base)).toBeNull();
    expect(draftProblem({ ...base, workflows: [] })).toContain("at least one");
    expect(draftProblem({ ...base, workflows: ["wf_document_only"], defaultWorkflow: "wf_document_selfie" })).toContain("default");
    expect(draftProblem({ ...base, displayName: "<b>" })).toContain("name customers see");
    expect(draftProblem({ ...base, displayName: "A" })).toContain("name customers see");
    expect(draftProblem({ ...base, supportUrl: "http://x.example" })).toContain("https");
    expect(draftProblem({ ...base, supportUrl: "nonsense" })).toContain("web address");
    expect(draftProblem({ ...base, locale: "english" })).toContain("language");
    expect(draftProblem({ ...base, origins: "http://evil.example" })).toContain("https://");
  });
  it("labels workflows with a fallback", () => {
    expect(workflowLabel("wf_document_selfie")).toBe("ID document and selfie");
    expect(workflowLabel("wf_new_thing")).toBe("new thing");
  });
});
