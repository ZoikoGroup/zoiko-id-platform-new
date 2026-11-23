import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { answerQuestion, looksSensitive, NOT_ENFORCED, NOTE, STARTERS, tokens } from "../src/engine";
import { GUIDE_LINKS, PAGES, linkId } from "../src/pages";
import type { RoleMatrix } from "../src/types";

const matrix = JSON.parse(readFileSync(new URL("./fixtures/role-matrix.json", import.meta.url), "utf8")) as RoleMatrix;
const ask = (text: string, ...roles: string[]) => answerQuestion(text, { matrix, roles });
const allActions = matrix.matrices.flatMap((g) => g.actions);

const PHRASE = {
  X: "run it yourself",
  S: "prepare or request it",
  A: "approve it, but you cannot start it yourself",
  R: "view it",
  T: "only for your own account",
} as const;

describe("every action for every role (the matrix is the source of truth)", () => {
  const roles = matrix.roles;
  for (const role of roles) {
    it(`"Can I <feature>?" answers exactly what the matrix gives ${role}`, () => {
      const wrong: string[] = [];
      for (const a of allActions) {
        const res = ask(`Can I ${a.feature}?`, role);
        const mode = a.permissions[role] ?? "-";
        const ok = res.actions[0] === a.action && (mode === "-" ? res.text.includes("not part of your role") : res.text.includes(PHRASE[mode]));
        if (!ok) wrong.push(`${role} / ${a.action} (${mode}) -> ${res.actions[0]} :: ${res.text.slice(0, 90)}`);
      }
      expect(wrong).toEqual([]);
    });
  }

  it("never promises an outcome: every permission answer carries the standing disclaimer", () => {
    for (const a of allActions) {
      const res = ask(`Can I ${a.feature}?`, "ADMIN");
      expect(res.text).toContain(NOTE);
      expect(res.text.toLowerCase()).not.toMatch(/\byou will be allowed|guaranteed|definitely|always allowed\b/);
    }
  });
});

describe("the questions people actually ask", () => {
  it("can I invite a viewer", () => {
    for (const [role, yes] of [["OWNER", true], ["ADMIN", true], ["OPERATOR", false], ["APPROVER", false], ["VIEWER", false]] as const) {
      const r = ask("Can I invite a viewer?", role);
      expect(r.intent).toBe("CAN_I");
      expect(r.actions[0]).toBe("a.invite-operator-or-viewer");
      expect(r.text.includes("run it yourself")).toBe(yes);
      if (!yes) expect(r.text).toMatch(/Owner and Admin can run it/);
    }
  });

  it("who can approve privileged role grants", () => {
    const r = ask("Who can approve role grants?", "VIEWER");
    expect(r.intent).toBe("WHO_CAN");
    expect(r.text).toMatch(/Owner and Approver can approve it/);
    expect(r.text).not.toMatch(/Admin can approve/);
  });

  it("a person who can only approve is told they cannot start it", () => {
    const r = ask("Can I approve a privileged role grant?", "APPROVER");
    expect(r.text).toContain("approve it, but you cannot start it yourself");
  });

  it("what can I do: lists the roles held and examples, and links to pages", () => {
    const r = ask("What can I do with my role?", "ADMIN", "APPROVER");
    expect(r.intent).toBe("ROLE_SUMMARY");
    expect(r.text).toContain("Admin and Approver");
    expect(r.text).toMatch(/You can run, for example/);
    expect(r.text).toMatch(/You can approve, for example/);
    expect(r.links.length).toBeGreaterThan(0);
  });

  it("a viewer's summary has nothing to run, request or approve", () => {
    const r = ask("what can I do", "VIEWER");
    expect(r.text).toContain("Viewer");
    expect(r.text).not.toMatch(/You can run, for example|You can approve, for example|You can prepare or request/);
  });

  it("a person with no company role still owns their own account but nothing else", () => {
    expect(ask("what can I do").text).toMatch(/without a company role/);
    expect(ask("Can I manage my own passkeys?").text).toMatch(/only for your own account/);
    expect(ask("Can I invite a viewer?").text).toMatch(/not part of your role/);
  });

  it("how do I get admin access: explains the request, the independent approval and the ban on self-elevation", () => {
    const r = ask("How do I get Admin access?", "OPERATOR");
    expect(r.intent).toBe("ELEVATION");
    expect(r.text).toMatch(/Nobody can give themselves a higher role/);
    expect(r.text).toMatch(/independent/);
    expect(r.links.map((l) => l.id)).toEqual(expect.arrayContaining(["PAGE-TEAM", "PAGE-APPROVALS"]));
  });

  it("what does a role mean, and how do two roles differ", () => {
    expect(ask("What does an approver do?", "VIEWER").text).toMatch(/independent checker/);
    expect(ask("What is an Owner?", "VIEWER").text).toMatch(/not the company's legal signing authority/);
    const d = ask("What is the difference between admin and owner?", "VIEWER");
    expect(d.intent).toBe("ROLE_COMPARE");
    expect(d.text).toMatch(/differ on \d+ of 68/);
  });

  it("where do I find a page", () => {
    expect(ask("Where do I manage my passkeys?", "VIEWER").links.map((l) => l.id)).toContain("PAGE-PASSKEYS");
    expect(ask("Where can I see invoices?", "OWNER").links.map((l) => l.id)).toContain("PAGE-BILLING");
    expect(ask("where do I invite team members", "ADMIN").links.map((l) => l.id)).toContain("PAGE-TEAM");
    expect(ask("how do I see audit logs", "OWNER").links.length).toBeGreaterThan(0);
  });

  it("an action nobody can take is said plainly", () => {
    for (const q of ["Can I read API keys?", "Can I see another user's password?", "Can I edit the identity ledger history?"]) {
      const r = ask(q, "OWNER");
      expect(r.text, q).toMatch(/No customer role can do this/);
    }
  });
});

describe("what the guide will not do", () => {
  it("declines to act, and says where and whether the role allows it", () => {
    const r = ask("Invite bob@example.com as a viewer", "ADMIN");
    expect(r.decision).toBe("REFUSE");
    expect(r.text).toMatch(/can't do that for you/);
    expect(r.text).toMatch(/run it yourself/);
    expect(r.links.map((l) => l.id)).toContain("PAGE-TEAM");
  });

  it("declines to look up company data, but points to the page", () => {
    const r = ask("How many members do I have?", "OWNER");
    expect(r.decision).toBe("REFUSE");
    expect(r.intent).toBe("DATA_LOOKUP");
    expect(r.text).toMatch(/can't look up your company's data/);
    expect(r.links.map((l) => l.id)).toContain("PAGE-TEAM");
  });

  it("declines account operations", () => {
    for (const q of ["reset my password", "Delete the user alice", "Approve the pending request for me"]) {
      expect(ask(q, "ADMIN").decision, q).toBe("REFUSE");
    }
  });

  it("does not read, echo or keep secrets", () => {
    const secrets = [
      "my password is hunter2-CANARY",
      "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
      "-----BEGIN " + "PRIVATE KEY----- MIIE",
      "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxwRJSMeKKF2QT4fwpMeJf36",
      "4111 1111 1111 1111",
      "my otp is 482913",
    ];
    for (const s of secrets) {
      const r = ask(`${s} can I invite a viewer?`, "ADMIN");
      expect(r.sensitive, s).toBe(true);
      expect(r.decision).toBe("REFUSE");
      expect(r.text).not.toContain("CANARY");
      expect(r.text).not.toContain("4111");
      expect(r.text).not.toContain("482913");
    }
    expect(looksSensitive("Can I invite a viewer?")).toBe(false);
    expect(looksSensitive("What does a one-time password do?")).toBe(false);
  });

  it("abstains on what it does not know instead of guessing", () => {
    for (const q of ["What's the weather in Paris?", "write me a poem", "asdf qwer zxcv"]) {
      const r = ask(q, "ADMIN");
      expect(r.decision, q).toBe("ABSTAIN");
      expect(r.text).toMatch(/Try:/);
    }
    expect(ask("hello", "ADMIN").intent).toBe("HELP");
    expect(ask("   ", "ADMIN").intent).toBe("EMPTY");
  });
});

describe("the tools it is built from", () => {
  it("tokenizes with stemming and synonyms", () => {
    expect(tokens("Inviting people")).toEqual(expect.arrayContaining(["invit", "member"]));
    expect(tokens("add a new user")).toEqual(expect.arrayContaining(["invit", "member"]));
    expect(tokens("the of a")).toEqual([]);
  });

  it("starters adapt to a viewer", () => {
    expect(STARTERS(["VIEWER"]).some((s) => /viewer/i.test(s.text))).toBe(true);
    for (const r of matrix.roles) expect(STARTERS([r]).length, r).toBe(4);
  });

  it("each role gets its own first question, and every starter gets a real answer", () => {
    const first = new Set(matrix.roles.map((r) => STARTERS([r])[0]?.label));
    expect(first.size).toBeGreaterThan(3);
    expect(STARTERS(["APPROVER"])[0]?.label).toMatch(/approve/i);
    expect(STARTERS(["OWNER", "ADMIN"]).some((s) => /invite/i.test(s.label))).toBe(false);
    for (const r of matrix.roles) for (const s of STARTERS([r])) {
      const res = ask(s.text, r);
      expect(res.decision, `${r}: ${s.text}`).toBe("ANSWER");
    }
  });

  it("links point only at fixed console paths", () => {
    const paths = PAGES.map((p) => p.href);
    expect(new Set(paths).size).toBe(paths.length);
    for (const p of PAGES) {
      expect(p.href).toMatch(/^\/[a-z0-9/-]+$/);
      expect(GUIDE_LINKS[linkId(p.id)]?.href).toBe(p.href);
    }
    expect(GUIDE_LINKS["PAGE-EVIL"]).toBeUndefined();
    // every link an answer can carry exists in the fixed map
    for (const a of allActions) for (const l of ask(`Can I ${a.feature}?`, "OWNER").links) expect(GUIDE_LINKS[l.id]).toBeDefined();
  });
});

describe("wording and links read cleanly", () => {
  it("has no stray punctuation or nested parentheses in the answers", () => {
    const qs = ["What can I do with my role?", "Can I invite a viewer?", "Who can approve role grants?", "How do I get Admin access?", "Can I read API keys?", "Where do I manage my passkeys?"];
    for (const roles of [[], ["VIEWER"], ["ADMIN"], ["ADMIN", "APPROVER"], ["OWNER"]]) {
      for (const q of qs) {
        const t = ask(q, ...roles).text;
        expect(t, `${roles}: ${q}`).not.toMatch(/; This|\(\w[^()]*\([^()]*\)\)|  | \./);
      }
    }
  });

  it("only names the roles individually when the person holds more than one", () => {
    expect(ask("Can I invite a viewer?", "ADMIN").text).not.toMatch(/Admin:/);
    expect(ask("Can I invite a viewer?", "ADMIN", "APPROVER").text).toMatch(/\(Admin: run it; Approver: no access\)/);
  });

  it("a role word in a feature name does not send the person to an unrelated page", () => {
    const links = ask("Can I invite a viewer?", "ADMIN").links.map((l) => l.id);
    expect(links).toContain("PAGE-TEAM");
    expect(links).not.toContain("PAGE-OPERATOR-ACCESS");
  });
});

describe("honesty about enforcement", () => {
  const withEnforcement = (enforced: boolean | undefined) => answerQuestion("Can I invite a viewer?", { matrix, roles: ["ADMIN"], enforced });

  it("says so while the owners keep the matrix in report-only mode", () => {
    const r = withEnforcement(false);
    expect(r.text).toContain(NOT_ENFORCED);
    expect(r.text.indexOf(NOTE)).toBeLessThan(r.text.indexOf(NOT_ENFORCED));
  });

  it("stays quiet when it is enforced, and when it is not known", () => {
    expect(withEnforcement(true).text).not.toContain(NOT_ENFORCED);
    expect(withEnforcement(undefined).text).not.toContain(NOT_ENFORCED);
  });
});

describe("wording and routing found by the all-roles check", () => {
  for (const role of ["OWNER", "ADMIN", "APPROVER", "OPERATOR", "VIEWER"]) {
    it(`never says "can not part of your role" (${role})`, () => {
      for (const q of ["where are invoices", "invite bob@example.com", "how many members do we have", "where do I manage the team"]) {
        expect(ask(q, role).text).not.toMatch(/can not part of/i);
      }
    });
  }

  it("inviting an Admin is a role-grant request, not the ordinary Operator/Viewer invitation", () => {
    expect(ask("Can I invite an admin?", "ADMIN").actions[0]).toBe("a.request-admin-approver-role-grant");
    expect(ask("Can I invite an admin?", "VIEWER").text).toContain("No.");
    expect(ask("Can I invite a viewer?", "ADMIN").actions[0]).toBe("a.invite-operator-or-viewer");
  });

  it("the role-grant explanation reads correctly", () => {
    const t = ask("How do I get Admin access?", "OPERATOR").text;
    expect(t).toContain("an Owner or Admin starts it");
    expect(t).toContain("independent Owner or Approver approves it");
  });

  it("deleting the company is not matched to an unrelated matrix row", () => {
    for (const role of ["OWNER", "ADMIN", "VIEWER"]) {
      const res = ask("Can I delete the company?", role);
      expect(res.decision).toBe("ABSTAIN");
      expect(res.actions).toEqual([]);
    }
  });
});
