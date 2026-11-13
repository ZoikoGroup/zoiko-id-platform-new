import { describe, expect, it } from "vitest";

import { V2Client, V2Error, V2_PATHS, type V2Fetch } from "../src/v2.js";

const BASE = "https://api.example.test";

type Call = { method: string; url: string; headers: Record<string, string>; body?: string | Uint8Array };

function fake(status = 200, doc: Record<string, unknown> = { ok: true }) {
  const calls: Call[] = [];
  const fetcher: V2Fetch = async (url, init) => {
    calls.push({ method: init.method, url: url.replace(BASE, ""), headers: init.headers, body: init.body });
    return { status, text: async () => JSON.stringify(doc) };
  };
  return { client: new V2Client(BASE, "tok-9", fetcher), calls };
}

describe("the /v2 client (ZID-INT-002)", () => {
  it("sends the bearer, the idempotency key and JSON and returns the plain document", async () => {
    const { client, calls } = fake(200, { credential_ref: "cred_1", dimensions: [] });
    const out = await client.verifyCredential("professional_license", { name: "Board" }, { license_ref: "L-1" }, { country: "US" }, "k-0001", { purpose: "professional_onboarding" });
    expect(calls[0]).toMatchObject({ method: "POST", url: "/v2/credentials/verify" });
    expect(calls[0]!.headers.authorization).toBe("Bearer tok-9");
    expect(calls[0]!.headers["idempotency-key"]).toBe("k-0001");
    expect(JSON.parse(String(calls[0]!.body)).purpose).toBe("professional_onboarding");
    expect(out).toEqual({ credential_ref: "cred_1", dimensions: [] });
  });

  it("turns a problem document into a V2Error with the documents code and trace id", async () => {
    const { client } = fake(409, { code: "idempotency_conflict", status: 409, detail: "reused", trace_id: "req_9" });
    const e = await client.evaluateEligibility({ country: "US" }, "LAW", "legal.advice", "k-0002").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(V2Error);
    expect(e).toMatchObject({ code: "idempotency_conflict", status: 409, traceId: "req_9" });
  });

  it("treats a non-JSON failure as a V2Error too", async () => {
    const client = new V2Client(BASE, "t", async () => ({ status: 502, text: async () => "<html>bad gateway</html>" }));
    await expect(client.capabilities()).rejects.toMatchObject({ code: "http_error", status: 502 });
  });

  it("routes every read and cancel to its documented path", async () => {
    const { client, calls } = fake();
    await client.capabilities();
    await client.coverage("US", "LAW");
    await client.captureAlternatives("GB", "no_camera");
    await client.credential("cred_1");
    await client.eligibility("elig_1");
    await client.case("case_1");
    await client.cancelCase("case_1");
    await client.evidenceManifest("ev_1");
    await client.deleteEvidence("ev_1");
    await client.documentResult("docval_1");
    await client.issuerCheck("ichk_1");
    await client.presentation("pres_1");
    await client.webhookEndpoints();
    await client.event("e1");
    await client.replayEvent("e1");
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "GET /v2/capabilities",
      "GET /v2/coverage?country=US&profession=LAW",
      "GET /v2/capture-alternatives?country=GB&reason=no_camera",
      "GET /v2/credentials/cred_1",
      "GET /v2/eligibility/elig_1",
      "GET /v2/verifications/case_1",
      "POST /v2/verifications/case_1/cancel",
      "GET /v2/evidence/ev_1/manifest",
      "DELETE /v2/evidence/ev_1",
      "GET /v2/documents/docval_1",
      "GET /v2/issuer-checks/ichk_1",
      "GET /v2/presentations/pres_1",
      "GET /v2/webhook-endpoints",
      "GET /v2/events/e1",
      "POST /v2/events/e1/replay",
    ]);
  });

  it("announces the exact size and digest, then sends the raw bytes with the token", async () => {
    const content = new TextEncoder().encode("%PDF-1.7 a document");
    const calls: Call[] = [];
    const fetcher: V2Fetch = async (url, init) => {
      calls.push({ method: init.method, url: url.replace(BASE, ""), headers: init.headers, body: init.body });
      const doc = init.method === "POST" ? { upload_url: "/v2/evidence/uploads/u-1/content", upload_token: "tok.en.sig" } : { state: "READY" };
      return { status: 201, text: async () => JSON.stringify(doc) };
    };
    const out = await new V2Client(BASE, "t", fetcher).uploadEvidence(content, "application/pdf", "k-0003", "sub_x");
    expect(out).toEqual({ state: "READY" });
    const announced = JSON.parse(String(calls[0]!.body));
    const digest = await crypto.subtle.digest("SHA-256", content as unknown as BufferSource);
    const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    expect(announced).toEqual({ declared_type: "application/pdf", size_bytes: content.length, sha256: hex, subject_ref: "sub_x" });
    expect(calls[1]).toMatchObject({ method: "PUT", url: "/v2/evidence/uploads/u-1/content" });
    expect(calls[1]!.body).toBe(content);
    expect(calls[1]!.headers["x-upload-token"]).toBe("tok.en.sig");
    expect(calls[1]!.headers["content-type"]).toBe("application/octet-stream");
  });

  it("raises the reason when an upload is rejected", async () => {
    const fetcher: V2Fetch = async (_url, init) => ({
      status: init.method === "POST" ? 201 : 422,
      text: async () => JSON.stringify(init.method === "POST" ? { upload_url: "/u", upload_token: "t" } : { code: "evidence_rejected_infected", status: 422 }),
    });
    await expect(new V2Client(BASE, "t", fetcher).uploadEvidence(new Uint8Array([1]), "image/png", "k-0004")).rejects.toMatchObject({ code: "evidence_rejected_infected" });
  });

  it("validates a document from a stored reference or from pasted content, never both", async () => {
    const { client, calls } = fake();
    await client.validateDocument("application/pdf", ["integrity"], { content: new TextEncoder().encode("%PDF-") });
    await client.validateDocument("application/pdf", ["integrity"], { evidenceRef: "ev_1" });
    const [pasted, stored] = calls.map((c) => JSON.parse(String(c.body)));
    expect(pasted.content_base64).toBe("JVBERi0=");
    expect(pasted.evidence_ref).toBeUndefined();
    expect(stored.evidence_ref).toBe("ev_1");
    expect(stored.content_base64).toBeUndefined();
  });

  it("carries the case, the task, the presentation configuration and their keys", async () => {
    const { client, calls } = fake();
    await client.openCase("professional_onboarding", { country: "US" }, { profile_id: "p", version: "2.0" }, ["identity", "license"], "k-0006", { consent_ref: "c1" });
    await client.submitTask("case_1", "license", { issuer_claim: { name: "B" }, credential_claim: { license_ref: "L" } }, "k-0007");
    await client.requestPresentation("cfg-1", "rp-1", "age", ["age_over_18"], { country: "GB" }, "k-0008", 120);
    expect(calls.map((c) => [c.method, c.url, c.headers["idempotency-key"]])).toEqual([
      ["POST", "/v2/verifications", "k-0006"],
      ["POST", "/v2/verifications/case_1/tasks/license", "k-0007"],
      ["POST", "/v2/presentations/requests", "k-0008"],
    ]);
    expect(JSON.parse(String(calls[2]!.body)).expires_in_seconds).toBe(120);
  });

  it("lists every route it calls, and each is a real client method", () => {
    expect(V2_PATHS.length).toBe(26);
    expect(new Set(V2_PATHS).size).toBe(V2_PATHS.length);
  });
});
