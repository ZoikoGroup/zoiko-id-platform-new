import type { AgentItem, BlueprintRelease, BuildItem, ToolItem, WorkloadBindingView } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { ACTION_GROUPS } from "../authority/choice-list";
import {
  ACTION_OPTIONS,
  BUILD_PASTE,
  BUILD_TEST,
  GATE_NOTES,
  buildChoices,
  digestFor,
  isApiClientBuild,
  knownRepositories,
  parseImage,
  repositoryOf,
  sortVersions,
  suggestBuildName,
  worthLookingUp,
  testBuildDigest,
  TOOL_PRESETS,
  forcesMandate,
  heldActions,
  manifestDigest,
  missingActions,
  nextVersion,
  setupSteps,
  slug,
  spiffeIdFor,
} from "./agents-logic";

describe("tool presets", () => {
  it("each one uses an action the dialog can grant, and matches the side-effect rule", () => {
    const known = new Set(ACTION_GROUPS.flatMap((g) => g.choices.map((c) => c.id)));
    for (const p of TOOL_PRESETS) {
      expect(known.has(p.action), p.action).toBe(true);
      expect(p.toolId, p.id).toMatch(/^[a-z0-9][a-z0-9._-]{1,62}$/);
      if (forcesMandate(p.effect)) expect(p.mandate, `${p.id} moves value, so it needs a mandate`).toBe(true);
    }
    expect(new Set(TOOL_PRESETS.map((p) => p.id)).size).toBe(TOOL_PRESETS.length);
  });

  it("offers every action with its feature area, so a person picks instead of typing", () => {
    expect(ACTION_OPTIONS.find((o) => o.id === "invoice.approve")?.description).toContain("Payments and invoices");
    expect(ACTION_OPTIONS.length).toBe(ACTION_GROUPS.flatMap((g) => g.choices).length);
  });
});

describe("nextVersion", () => {
  it("starts at 1.0.0 and raises the patch of the highest release", () => {
    expect(nextVersion([])).toBe("1.0.0");
    expect(nextVersion(["1.0.0"])).toBe("1.0.1");
    expect(nextVersion(["1.2.9", "1.10.0", "1.2.10"])).toBe("1.10.1");
    expect(nextVersion(["2.0.0", "1.9.9"])).toBe("2.0.1");
  });
  it("ignores versions that are not x.y.z", () => {
    expect(nextVersion(["1", "latest", "v2"])).toBe("1.0.0");
    expect(nextVersion(["1", "3.1.4"])).toBe("3.1.5");
  });
});

describe("manifestDigest", () => {
  const a = { tool_id: "invoices.approve", version: "1.0.0" };
  const b = { tool_id: "payments.release", version: "1.0.0" };

  it("has the shape the cell requires", async () => {
    expect(await manifestDigest([a, b])).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
  it("does not depend on the order the tools were ticked in", async () => {
    expect(await manifestDigest([a, b])).toBe(await manifestDigest([b, a]));
  });
  it("changes when the tools or a version change", async () => {
    const base = await manifestDigest([a, b]);
    expect(await manifestDigest([a])).not.toBe(base);
    expect(await manifestDigest([a, { ...b, version: "1.0.1" }])).not.toBe(base);
  });
});

describe("what the person's grants hold", () => {
  it("reports exactly the actions that are missing", () => {
    expect(missingActions(["machine.agent.read", "mandate.read"], ["machine.tool.manage"])).toEqual(["machine.tool.manage"]);
    expect(missingActions(["machine.tool.manage"], ["machine.tool.manage"])).toEqual([]);
  });
  it("treats a grant with no action limit as holding everything, and honours wildcards", () => {
    expect(missingActions(null, ["machine.tool.manage"])).toEqual([]);
    expect(missingActions(["machine.*"], ["machine.tool.manage", "machine.agent.activate"])).toEqual([]);
    expect(missingActions(["machine.*"], ["mandate.create"])).toEqual(["mandate.create"]);
    expect(missingActions(["*"], ["anything.at.all"])).toEqual([]);
  });
  it("combines the grants, and any unlimited one wins", () => {
    expect(heldActions([{ actions: ["a.b"] }, { actions: ["c.d", "a.b"] }])?.sort()).toEqual(["a.b", "c.d"]);
    expect(heldActions([{ actions: ["a.b"] }, { actions: null }])).toBeNull();
    expect(heldActions([])).toEqual([]);
  });
});

const tool = (status: ToolItem["status"]) => ({ status }) as ToolItem;
const bp = (review_status: BlueprintRelease["review_status"]) => ({ review_status }) as BlueprintRelease;
const agent = (id: string, status: AgentItem["status"]) => ({ agent_principal_id: id, status }) as AgentItem;
const binding = (id: string, status: WorkloadBindingView["status"]) => ({ logical_principal_id: id, status }) as WorkloadBindingView;

describe("the setup guide", () => {
  const ids = (s: ReturnType<typeof setupSteps>) => s.filter((x) => !x.done).map((x) => x.id);

  it("starts with everything to do on an empty company", () => {
    expect(ids(setupSteps({ tools: [], blueprints: [], agents: [], bindings: [] }))).toEqual(["tools", "blueprint", "agent", "workload", "activate"]);
  });
  it("counts only active tools and approved blueprints as done", () => {
    const s = setupSteps({ tools: [tool("DRAFT")], blueprints: [bp("DRAFT")], agents: [], bindings: [] });
    expect(ids(s)).toContain("tools");
    expect(ids(s)).toContain("blueprint");
  });
  it("wants a runtime binding for every open agent, and ignores retired or revoked ones", () => {
    const base = { tools: [tool("ACTIVE")], blueprints: [bp("APPROVED")] };
    const unbound = setupSteps({ ...base, agents: [agent("a1", "DRAFT"), agent("a2", "REVOKED")], bindings: [] });
    expect(unbound.find((x) => x.id === "workload")?.done).toBe(false);
    const bound = setupSteps({ ...base, agents: [agent("a1", "DRAFT"), agent("a2", "REVOKED")], bindings: [binding("a1", "ACTIVE")] });
    expect(bound.find((x) => x.id === "workload")?.done).toBe(true);
    expect(setupSteps({ ...base, agents: [agent("a1", "DRAFT")], bindings: [binding("a1", "REVOKED")] }).find((x) => x.id === "workload")?.done).toBe(false);
  });
  it("is finished when an agent is active", () => {
    const s = setupSteps({ tools: [tool("ACTIVE")], blueprints: [bp("APPROVED")], agents: [agent("a1", "ACTIVE")], bindings: [binding("a1", "ACTIVE")] });
    expect(ids(s)).toEqual([]);
  });
});

describe("SPIFFE ids", () => {
  it("carry the tenant in the path and a safe namespace and account", () => {
    const id = spiffeIdFor({ trustDomain: "local-1.local.id.zoiko", tenantId: "01a1a5db-ae65-7297-b064-0dab77f76a19", namespace: "Payments", account: "Acme Invoice Agent" });
    expect(id).toBe("spiffe://local-1.local.id.zoiko/t/01a1a5db-ae65-7297-b064-0dab77f76a19/ns/payments/sa/acme-invoice-agent");
  });
  it("falls back to plain names when nothing usable was typed", () => {
    expect(spiffeIdFor({ trustDomain: "d.id.zoiko", tenantId: "t", namespace: "!!!", account: "" })).toBe("spiffe://d.id.zoiko/t/t/ns/agents/sa/agent");
    expect(slug("  A  b/c  ")).toBe("a-b-c");
  });
});

describe("suggested notes for the activation gates", () => {
  it("cover each of the four gates a person attests, with distinct choices", () => {
    expect(Object.keys(GATE_NOTES).sort()).toEqual(["KILL_PATH_TESTED", "OBSERVABILITY", "SECURITY_REVIEW", "SHORT_LIVED_CREDENTIALS"]);
    for (const [gate, notes] of Object.entries(GATE_NOTES)) {
      expect(notes.length, gate).toBeGreaterThan(0);
      expect(new Set(notes.map((n) => n.id)).size, gate).toBe(notes.length);
    }
  });
});

describe("choosing a build without knowing a digest", () => {
  const rel = (blueprint_id: string, version: string, d: string) => ({ blueprint_id, version, artifact_digest: `sha256:${d.repeat(64)}` });
  const build = (name: string, d: string, review_status = "APPROVED") => ({ name, artifact_digest: `sha256:${d.repeat(64)}`, review_status });
  const none = { builds: [], releases: [] as ReturnType<typeof rel>[], enforced: false, allowTest: true };

  it("offers approved builds by name first, then earlier releases' builds once each, the test build, then pasting", () => {
    const opts = buildChoices({
      ...none,
      builds: [build("Invoice bot image", "a"), build("Draft only", "d", "DRAFT"), build("Old image", "e", "REVOKED")],
      releases: [rel("bot", "1.0.0", "a"), rel("bot", "1.0.1", "c"), rel("other", "1.0.0", "c")],
    });
    expect(opts.map((o) => o.label)).toEqual(["Invoice bot image", "Same build as other 1.0.0", "A test build (for trying things out)", "A build from my registry…"]);
  });
  it("where approved builds are required, offers nothing but approved builds", () => {
    const opts = buildChoices({ ...none, enforced: true, builds: [build("Invoice bot image", "a"), build("Draft only", "d", "DRAFT")], releases: [rel("bot", "1.0.0", "c")] });
    expect(opts.map((o) => o.label)).toEqual(["Invoice bot image"]);
    expect(buildChoices({ ...none, enforced: true })).toEqual([]);
  });
  it("does not offer a test build when production forbids it", () => {
    expect(buildChoices({ ...none, allowTest: false }).map((o) => o.id)).toEqual([BUILD_PASTE]);
    expect(buildChoices(none).map((o) => o.id)).toEqual([BUILD_TEST, BUILD_PASTE]);
  });
  it("turns each choice into the digest it stands for", async () => {
    const d = `sha256:${"c".repeat(64)}`;
    const opts = buildChoices({ ...none, builds: [build("Invoice bot image", "c")] });
    expect(digestFor(opts[0]!.id, "", undefined)).toBe(d);
    expect(digestFor(BUILD_PASTE, `  ${d}  `, undefined)).toBe(d);
    expect(digestFor(BUILD_PASTE, "", undefined)).toBeNull();
    expect(digestFor(BUILD_TEST, "", undefined)).toBeNull();
    const t = await testBuildDigest("bot", "1.0.0");
    expect(digestFor(BUILD_TEST, "", t)).toBe(t);
    expect(digestFor("", "", undefined)).toBeNull();
  });
  it("gives a test build a stable digest of the right shape that differs per release", async () => {
    const a = await testBuildDigest("bot", "1.0.0");
    expect(a).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(await testBuildDigest("bot", "1.0.0")).toBe(a);
    expect(await testBuildDigest("bot", "1.0.1")).not.toBe(a);
  });
});

describe("the build step of the setup guide", () => {
  const base = { tools: [tool("ACTIVE")], blueprints: [], agents: [], bindings: [] };
  const b = (review_status: BuildItem["review_status"]) => ({ review_status }) as BuildItem;
  const ids = (s: ReturnType<typeof setupSteps>) => s.map((x) => x.id);

  it("is absent where builds are not required and none exist", () => {
    expect(ids(setupSteps({ ...base, builds: [], buildsEnforced: false }))).not.toContain("build");
  });
  it("is the second step where builds are required, and open until one is approved", () => {
    const s = setupSteps({ ...base, builds: [b("DRAFT")], buildsEnforced: true });
    expect(ids(s).slice(0, 3)).toEqual(["tools", "build", "blueprint"]);
    expect(s.find((x) => x.id === "build")).toMatchObject({ done: false, tab: "builds" });
    expect(setupSteps({ ...base, builds: [b("DRAFT"), b("APPROVED")], buildsEnforced: true }).find((x) => x.id === "build")?.done).toBe(true);
  });
  it("appears once builds exist even where they are not required", () => {
    expect(ids(setupSteps({ ...base, builds: [b("APPROVED")], buildsEnforced: false }))).toContain("build");
  });
});

describe("registering a build from one field", () => {
  const d = `sha256:${"a".repeat(64)}`;
  it("suggests a name people recognise from the image", () => {
    expect(suggestBuildName("ghcr.io/acme/invoice-bot:1.4")).toBe("Invoice bot 1.4");
    expect(suggestBuildName("ghcr.io/acme/invoice_agent")).toBe("Invoice agent");
    expect(suggestBuildName("ghcr.io/acme/bot:latest")).toBe("Bot");
    expect(suggestBuildName(`ghcr.io/acme/bot:2@${d}`)).toBe("Bot 2");
    expect(suggestBuildName("???")).toBe("Build");
  });
  it("takes the digest from a name that already carries one, and asks the registry for the rest", () => {
    expect(parseImage(`ghcr.io/acme/bot@${d}`)).toEqual({ image: "ghcr.io/acme/bot", digest: d, name: "Bot" });
    expect(parseImage("ghcr.io/acme/bot:1.4")).toEqual({ image: "ghcr.io/acme/bot:1.4", digest: null, name: "Bot 1.4" });
    expect(parseImage("   ")).toBeNull();
  });
  it("looks up only what is shaped like a registry image name", () => {
    expect(worthLookingUp("ghcr.io/acme/bot:1.4")).toBe(true);
    expect(worthLookingUp("registry.example.com:5000/team/bot")).toBe(true);
    expect(worthLookingUp("ghcr.io/ac")).toBe(true);
    expect(worthLookingUp("bot")).toBe(false);
    expect(worthLookingUp("ghcr.io/")).toBe(false);
    expect(worthLookingUp("ghcr.io/acme/bot with spaces")).toBe(false);
  });
});

describe("picking the image instead of typing it", () => {
  it("reads the repository out of a reference with a tag or a digest", () => {
    expect(repositoryOf("ghcr.io/acme/bot:1.4")).toBe("ghcr.io/acme/bot");
    expect(repositoryOf(`ghcr.io/acme/bot@sha256:${"a".repeat(64)}`)).toBe("ghcr.io/acme/bot");
    expect(repositoryOf("registry.example.com:5000/team/bot")).toBe("registry.example.com:5000/team/bot");
    expect(repositoryOf("registry.example.com:5000/team/bot:2")).toBe("registry.example.com:5000/team/bot");
  });
  it("offers the operator's repositories, then those already registered, each once and none that is not an image", () => {
    const builds = [{ source_ref: "ghcr.io/acme/bot:1.3" }, { source_ref: "ghcr.io/acme/other:2" }, { source_ref: null }, { source_ref: "nonsense" }];
    expect(knownRepositories(["ghcr.io/acme/bot"], builds)).toEqual(["ghcr.io/acme/bot", "ghcr.io/acme/other"]);
    expect(knownRepositories([], [])).toEqual([]);
  });
  it("orders versions newest first, by number, with latest last", () => {
    expect(sortVersions(["1.9.0", "1.10.0", "latest", "v2.0.0", "sha-abc", "1.2"])).toEqual(["v2.0.0", "1.10.0", "1.9.0", "1.2", "sha-abc", "latest"]);
    expect(sortVersions([])).toEqual([]);
  });
});

describe("isApiClientBuild (CR-142)", () => {
  const D = `sha256:${"c".repeat(64)}`;
  const builds = [
    { artifact_digest: `sha256:${"a".repeat(64)}`, kind: "IMAGE" },
    { artifact_digest: D, kind: "API_CLIENT" },
    { artifact_digest: `sha256:${"b".repeat(64)}` }, // an older server sends no kind: it is an image
  ];
  it("is true only for the build behind the release when that build is an API client", () => {
    expect(isApiClientBuild(builds, D)).toBe(true);
    expect(isApiClientBuild(builds, `sha256:${"a".repeat(64)}`)).toBe(false);
    expect(isApiClientBuild(builds, `sha256:${"b".repeat(64)}`)).toBe(false);
  });
  it("matches a digest spelled with the dash and in any case", () => {
    expect(isApiClientBuild(builds, `SHA-256:${"C".repeat(64)}`)).toBe(true);
  });
  it("is false with no digest, no builds, or a digest nothing registered", () => {
    expect(isApiClientBuild(builds, undefined)).toBe(false);
    expect(isApiClientBuild([], D)).toBe(false);
    expect(isApiClientBuild(builds, `sha256:${"d".repeat(64)}`)).toBe(false);
  });
});
