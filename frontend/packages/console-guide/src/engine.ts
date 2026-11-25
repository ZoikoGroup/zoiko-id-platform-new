/** The console guide's answer engine: deterministic, offline, and read-only.
 *
 * It answers questions about the signed-in person's own role from the published role matrix: what they can do, who can do something,
 * what a role is, why something is missing, and where a page lives. It has no model, makes no network call, reads no company data and
 * changes nothing. It never says that something WILL be allowed: the matrix is a projection "for navigation", and the platform decides
 * every request (scope, approvals, step-up). Anything outside that scope is declined with a pointer to the right page. */

import { PAGES, linkId, type GuidePage } from "./pages";
import type { GuideAnswer, GuideContext, GuideLink, MatrixAction, Mode, RoleMatrix } from "./types";

export const NOTE = "This is what your role allows in principle. The platform still checks scope, approvals and security every time.";
export const NOT_ENFORCED = "The role matrix is not enforced everywhere yet, so what the console allows today can differ from this.";

const ROLE_ORDER = ["OWNER", "ADMIN", "APPROVER", "OPERATOR", "VIEWER"] as const;
const MODE_RANK: Record<Mode, number> = { X: 5, A: 4, S: 3, R: 2, T: 1, "-": 0 };

const ROLE_TEXT: Record<string, string> = {
  OWNER:
    "The Owner administers the company in the console: members, settings, verification and billing. The Owner is not the company's legal signing authority, and sensitive changes still need an independent approval.",
  ADMIN:
    "An Admin runs day-to-day administration: inviting and removing ordinary members, managing sessions and integrations. An Admin cannot approve privileged changes; an Owner or Approver does that.",
  APPROVER:
    "An Approver is an independent checker. Approvers vote on privileged requests such as role grants, ownership transfers and policy changes, and can read most areas, but they do not start those changes.",
  OPERATOR:
    "An Operator does hands-on work inside an approved scope: preparing evidence, requesting changes and running ordinary approved operations.",
  VIEWER: "A Viewer can read an approved, redacted overview and manage their own account. A Viewer cannot change company settings.",
};

// ----------------------------------------------------------------------------------------------------------------- text helpers
const STOP = new Set(
  (
    "a an the i me my we our you your can could should would may might do does did is are am be been to of for in on at by with and or not it its this that these those " +
    "what which who whom how why where when need want like please about any have has had get gets also there their them then than so if as from into out up over " +
    "under again once here very just only own same too both each other some such no nor allowed able allow permission permissions right rights role roles " +
    "tell show explain give let know much many more most really actually thing things"
  ).split(" "),
);

function stem(raw: string): string {
  let w = raw;
  if (w.length > 4 && w.endsWith("ies")) w = `${w.slice(0, -3)}y`;
  else if (w.length > 4 && /(ss|sh|ch|x)es$/.test(w)) w = w.slice(0, -2);
  else if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) w = w.slice(0, -1);
  if (w.length > 5 && w.endsWith("ing")) w = w.slice(0, -3);
  else if (w.length > 4 && w.endsWith("ed")) w = w.slice(0, -2);
  if (w.length > 4 && w.endsWith("e")) w = w.slice(0, -1);
  return w;
}

/** Words people use for matrix vocabulary. Keys are written as people write them; they are stemmed once so that "invoices" and "invoice" both hit. */
const SYNONYM_SOURCE: Record<string, string> = {
  add: "invite",
  user: "member",
  people: "member",
  person: "member",
  employee: "member",
  teammate: "member",
  colleague: "member",
  delete: "remove",
  kick: "remove",
  fire: "remove",
  offboard: "remove",
  log: "audit",
  download: "export",
  mfa: "factor",
  "2fa": "factor",
  token: "credential",
  money: "billing",
  invoice: "billing",
  payment: "billing",
  another: "other",
};
const SYNONYM = new Map<string, string>(Object.entries(SYNONYM_SOURCE).map(([k, v]) => [stem(k), stem(v)]));

export function tokens(text: string): string[] {
  const clean = text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  if (!clean) return [];
  const out: string[] = [];
  for (const raw of clean.split(" ")) {
    if (raw.length < 2 || STOP.has(raw)) continue;
    const s = stem(raw);
    out.push(SYNONYM.get(s) ?? s);
  }
  return out;
}

// ----------------------------------------------------------------------------------------------------------------- sensitive input
const SECRET_PATTERNS: RegExp[] = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/i,
  /\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{4,}\b/,
  /\bbearer\s+[\w.~+/=-]{16,}/i,
  /\b(?:authorization|cookie)\s*:\s*\S+/i,
  /\b(?:password|passwd|passcode|secret|api[ _-]?key|token)\b\s*(?:is|=|:)\s*\S{4,}/i,
  /\b(?:otp|verification code|one[- ]time (?:code|password)|recovery code)\b[^0-9\n]{0,24}\b\d{4,8}\b/i,
  /\b(?:sk|pk|rk)[-_](?:live|test)?[-_]?[A-Za-z0-9]{16,}\b/,
  /\b[A-Za-z0-9+/_-]{60,}={0,2}/,
];

export function looksSensitive(text: string): boolean {
  const t = text.normalize("NFKC");
  if (SECRET_PATTERNS.some((p) => p.test(t))) return true;
  for (const m of t.matchAll(/\b(?:\d[ -]?){13,19}\b/g)) {
    const digits = m[0].replace(/\D/g, "");
    if (digits.length >= 13 && luhn(digits)) return true;
  }
  return false;
}

function luhn(d: string): boolean {
  let sum = 0;
  let alt = false;
  for (let i = d.length - 1; i >= 0; i--) {
    let n = d.charCodeAt(i) - 48;
    if (alt) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    sum += n;
    alt = !alt;
  }
  return sum % 10 === 0;
}

// ----------------------------------------------------------------------------------------------------------------- index
interface Doc {
  id: string;
  weights: Map<string, number>;
}

class Index {
  private readonly docs: Doc[] = [];
  private readonly idf = new Map<string, number>();

  constructor(entries: { id: string; fields: { text: string; weight: number }[] }[]) {
    const df = new Map<string, number>();
    for (const e of entries) {
      const weights = new Map<string, number>();
      for (const f of e.fields) for (const t of tokens(f.text)) weights.set(t, Math.max(weights.get(t) ?? 0, f.weight));
      this.docs.push({ id: e.id, weights });
      for (const t of weights.keys()) df.set(t, (df.get(t) ?? 0) + 1);
    }
    const n = Math.max(this.docs.length, 1);
    for (const [t, d] of df) this.idf.set(t, Math.log(1 + n / d));
  }

  search(query: string[], limit = 3): { id: string; score: number; matched: number }[] {
    const q = [...new Set(query)];
    if (q.length === 0) return [];
    const out: { id: string; score: number; matched: number }[] = [];
    for (const d of this.docs) {
      let score = 0;
      let matched = 0;
      for (const t of q) {
        const w = d.weights.get(t);
        if (w) {
          score += (this.idf.get(t) ?? 1) * w;
          matched++;
        }
      }
      if (matched > 0) out.push({ id: d.id, score, matched });
    }
    return out.sort((a, b) => b.score - a.score).slice(0, limit);
  }
}

/** Words people use for a few rows that the matrix names differently. */
const ALIASES: Record<string, string> = {
  "c.request-reset-of-another-user-s-factors": "reset recover forgot lost mfa passkey another user locked out",
  "c.access-other-users-raw-passwords-secrets": "see view read show another user password secret",
  "a.invite-operator-or-viewer": "add new user member teammate invitation",
  "a.disable-remove-ordinary-member": "remove delete kick offboard user member",
  "f.view-billing-records": "invoices payments plan subscription cost",
  "f.view-authorized-audit-and-ledger-entries": "logs audit trail history events",
  "f.request-sensitive-evidence-export": "export download logs audit evidence",
  "e.read-api-keys-private-keys-or-access-tokens": "see view read show api key token secret",
};

const pageIndex = new Index(PAGES.map((p) => ({ id: p.id, fields: [{ text: p.label, weight: 2 }, { text: p.keywords, weight: 2 }] })));
const indexCache = new WeakMap<RoleMatrix, { index: Index; byId: Map<string, MatrixAction> }>();

function actionIndex(m: RoleMatrix) {
  let c = indexCache.get(m);
  if (!c) {
    const all = m.matrices.flatMap((g) => g.actions.map((a) => ({ a, group: g.title })));
    c = {
      byId: new Map(all.map((x) => [x.a.action, x.a])),
      index: new Index(
        all.map(({ a, group }) => ({ id: a.action, fields: [{ text: a.feature, weight: 3 }, { text: ALIASES[a.action] ?? "", weight: 2 }, { text: a.rule, weight: 1 }, { text: group, weight: 0.5 }] })),
      ),
    };
    indexCache.set(m, c);
  }
  return c;
}

/** The best matrix actions for a question, or none when the match is too weak to trust. */
function findActions(m: RoleMatrix, query: string[]): MatrixAction[] {
  const { index, byId } = actionIndex(m);
  const hits = index.search(query, 4);
  const best = hits[0];
  if (!best) return [];
  const strong = best.matched >= 2 || best.score >= 5;
  if (!strong) return [];
  const floor = best.score * 0.6;
  return hits
    .filter((h) => h.score >= floor)
    .map((h) => byId.get(h.id))
    .filter((a): a is MatrixAction => a !== undefined);
}

function findPage(query: string[], weak = false): GuidePage | undefined {
  const hit = pageIndex.search(query, 1)[0];
  if (!hit) return undefined;
  if (weak ? hit.score < 2 : hit.matched < 2 && hit.score < 4) return undefined;
  return PAGES.find((p) => p.id === hit.id);
}

// ----------------------------------------------------------------------------------------------------------------- phrasing
const MODE_PHRASE: Record<Mode, string> = {
  X: "run it yourself, within your approved scope",
  S: "prepare or request it, but someone else has to approve or complete it",
  A: "approve it, but you cannot start it yourself",
  R: "view it (read access within your scope)",
  T: "do this only for your own account",
  "-": "not part of your role",
};
/** "your role can run it ..." for a mode the role has; "your role does not include this" when it has none (never "can not part of your role"). */
const roleCan = (mode: Mode): string => (mode === "-" ? "does not include this" : `can ${MODE_PHRASE[mode]}`);
const ROLE_NAME = (r: string): string => r.charAt(0) + r.slice(1).toLowerCase();
const MODE_LABEL: Record<Mode, string> = { X: "run it", S: "request it", A: "approve it", R: "view it", T: "own account only", "-": "no access" };
const ROLE_WORDS_TOKENS = new Set(["owner", "admin", "approver", "operator", "viewer"]);

function titleCase(a: MatrixAction): string {
  return a.feature;
}

/** The caller's mode for an action, per role (a person with no matrix role still owns their own account). */
function modesFor(a: MatrixAction, roles: string[]): { role: string; mode: Mode }[] {
  if (roles.length === 0) {
    const own = Object.values(a.permissions).includes("T");
    return [{ role: "MEMBER", mode: own ? "T" : "-" }];
  }
  return roles.map((r) => ({ role: r, mode: a.permissions[r] ?? "-" }));
}

function best(modes: { mode: Mode }[]): Mode {
  return modes.reduce<Mode>((acc, m) => (MODE_RANK[m.mode] > MODE_RANK[acc] ? m.mode : acc), "-");
}

function rolesByMode(a: MatrixAction): Map<Mode, string[]> {
  const out = new Map<Mode, string[]>();
  for (const r of ROLE_ORDER) {
    const mode = a.permissions[r];
    if (!mode || mode === "-") continue;
    out.set(mode, [...(out.get(mode) ?? []), r]);
  }
  return out;
}

function joinNames(items: string[]): string {
  const n = items.map(ROLE_NAME);
  if (n.length <= 1) return n.join("");
  return `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`;
}

function joinOr(items: string[]): string {
  const n = items.map(ROLE_NAME);
  if (n.length <= 1) return n.join("");
  return `${n.slice(0, -1).join(", ")} or ${n[n.length - 1]}`;
}

function who(a: MatrixAction): string {
  const g = rolesByMode(a);
  if (g.size === 0) return "No customer role can do this. It is not available to anyone in the console.";
  const parts: string[] = [];
  const phrases: Record<Mode, string> = {
    X: "can run it",
    S: "can prepare or request it",
    A: "can approve it",
    R: "can view it",
    T: "can do it for their own account",
    "-": "",
  };
  for (const mode of ["X", "S", "A", "R", "T"] as Mode[]) {
    const r = g.get(mode);
    if (r?.length) parts.push(`${joinNames(r)} ${phrases[mode]}`);
  }
  return `${parts.join("; ")}.`;
}

function link(p: GuidePage): GuideLink {
  return { id: linkId(p.id), label: p.label };
}

function pagesFor(a: MatrixAction): GuidePage[] {
  const q = tokens(`${a.feature} ${a.feature}`).filter((t) => !ROLE_WORDS_TOKENS.has(t)); // a role word in a feature name is not a page
  return pageIndex
    .search(q, 2)
    .filter((h) => h.score >= 3)
    .map((h) => PAGES.find((p) => p.id === h.id))
    .filter((p): p is GuidePage => p !== undefined);
}

function ans(intent: string, text: string, opts: Partial<GuideAnswer> = {}): GuideAnswer {
  return { decision: "ANSWER", intent, text, links: [], actions: [], ...opts };
}

// ----------------------------------------------------------------------------------------------------------------- intents
const RX = {
  greeting: /^\s*(hi|hello|hey|help|start|menu)\b[\s!.?]*$/i,
  summary: /\b(what can i do|what am i allowed|what do i have access|my (own )?(permission|role|access|right)s?|what (is|are) my (role|permission|access)|which role am i|what role do i have|what can i see)\b/i,
  who: /\b(who can|who is allowed|who may|which roles?|what roles?|who has|who gets|who approves|who decides)\b/i,
  canI: /\b(can i|could i|may i|am i allowed|am i able|do i have (access|permission|the right)|why can'?t i|why cannot i|why am i not|why don'?t i|i can'?t|i cannot|not allowed|no permission|missing)\b/i,
  where: /\b(where (do|can|is|are)|how (do|can|should) i|take me to|open the|go to|find the|navigate)\b/i,
  elevate: /\b(get|become|be made|make me|promote me|need|want|request|ask for) (an? |the )?(more access|admin|administrator|owner|approver|operator|higher role|elevated)|\bgive me (admin|owner|access)|\bhow (do|can) i (become|get) (an? )?(admin|owner|approver)/i,
  compare: /\b(difference|differ|vs\.?|versus|compare|between)\b/i,
  describeRole: /\b(what (is|does|are) (an? |the )?(owner|admin|administrator|approver|operator|viewer)|(owner|admin|administrator|approver|operator|viewer)s? (do|does|can do|role))\b/i,
  action: /^\s*(please\s+)?(invite|remove|delete|approve|revoke|reset|change|update|create|disable|suspend|export|rotate|close|grant|add|send|unlock|transfer|cancel|block)\b/i,
  data: /\b(how many|list (all )?(my|our|the)|show (me )?(my|our|all)|what (are|were) (my|our)|who are (my|our)|count (my|our))\b/i,
};

const ROLE_WORDS: [string, RegExp][] = [
  ["OWNER", /\bowners?\b/i],
  ["ADMIN", /\badmin(istrator)?s?\b/i],
  ["APPROVER", /\bapprovers?\b/i],
  ["OPERATOR", /\boperators?\b/i],
  ["VIEWER", /\bviewers?\b/i],
];

function mentionedRoles(text: string): string[] {
  return ROLE_WORDS.filter(([, rx]) => rx.test(text)).map(([r]) => r);
}

function describeCounts(m: RoleMatrix, role: string): string {
  const c: Record<string, number> = { X: 0, S: 0, A: 0, R: 0, T: 0 };
  for (const g of m.matrices) for (const a of g.actions) {
    const mode = a.permissions[role];
    if (mode && mode !== "-") c[mode] = (c[mode] ?? 0) + 1;
  }
  return `In the permission matrix, the ${ROLE_NAME(role)} can run ${c.X} things, prepare or request ${c.S}, approve ${c.A} and view ${c.R}, plus ${c.T} that concern only their own account.`;
}

function examples(m: RoleMatrix, role: string, mode: Mode, n = 3): string[] {
  const out: string[] = [];
  for (const g of m.matrices) for (const a of g.actions) if (a.permissions[role] === mode && out.length < n) out.push(titleCase(a));
  return out;
}

type Starter = { label: string; text: string; description?: string; icon?: string };

const WHAT_CAN_I_DO: Starter = { label: "What can I do?", text: "What can I do with my role?", description: "Your role and what it allows.", icon: "user" };
const PASSKEYS: Starter = { label: "Manage my passkeys", text: "Where do I manage my passkeys?", description: "Sign-in and recovery settings.", icon: "lock" };

/** The first cards a person sees. They follow the person's highest role, so each role is offered the questions that matter to it. */
const ROLE_STARTERS: Record<string, Starter[]> = {
  OWNER: [
    WHAT_CAN_I_DO,
    { label: "Grant a role", text: "How do I grant a role?", description: "Where role grants start.", icon: "share" },
    { label: "Role approvals", text: "Who can approve role grants?", description: "Who signs off on access changes.", icon: "shield" },
    PASSKEYS,
  ],
  ADMIN: [
    WHAT_CAN_I_DO,
    { label: "Invite someone", text: "Can I invite a viewer?", description: "Who you can bring into your team.", icon: "share" },
    { label: "Role approvals", text: "Who can approve role grants?", description: "Who signs off on access changes.", icon: "shield" },
    PASSKEYS,
  ],
  APPROVER: [
    { label: "What can I approve?", text: "What can I approve with my role?", description: "Your role and what it allows.", icon: "user" },
    { label: "Approve a role grant", text: "Can I approve a role grant?", description: "Sign off on access changes.", icon: "shield" },
    { label: "Approve my own request?", text: "Can I approve my own request?", description: "How independent approval works.", icon: "share" },
    PASSKEYS,
  ],
  OPERATOR: [
    { label: "What can I run?", text: "What can I do with my role?", description: "Your role and what it allows.", icon: "user" },
    { label: "What can I prepare?", text: "What can I prepare or request?", description: "Work that others approve.", icon: "share" },
    { label: "Can I approve?", text: "Can I approve a role grant?", description: "Who signs off on access changes.", icon: "shield" },
    PASSKEYS,
  ],
  VIEWER: [
    { label: "What can I see?", text: "What can a viewer do?", description: "What read-only access covers.", icon: "document" },
    { label: "Who approves access?", text: "Who can approve role grants?", description: "Who signs off on access changes.", icon: "shield" },
    { label: "Get more access", text: "How do I get Operator access?", description: "How a role change is requested.", icon: "share" },
    PASSKEYS,
  ],
};

export const STARTERS = (roles: string[]): Starter[] => {
  const top = ROLE_ORDER.find((r) => roles.includes(r)) ?? "VIEWER";
  return [...(ROLE_STARTERS[top] ?? ROLE_STARTERS["VIEWER"] ?? [])];
};

// ----------------------------------------------------------------------------------------------------------------- the engine
/** Answers a question. While the owners keep the matrix in report-only mode, every permission answer says so: the guide describes the
 * published policy, and must not imply that the platform already enforces it everywhere. */
export function answerQuestion(rawText: string, ctx: GuideContext): GuideAnswer {
  const res = answerCore(rawText, ctx);
  if (ctx.enforced === false && res.text.includes(NOTE)) return { ...res, text: res.text.replace(NOTE, `${NOTE} ${NOT_ENFORCED}`) };
  return res;
}

function answerCore(rawText: string, ctx: GuideContext): GuideAnswer {
  const text = rawText.normalize("NFKC").trim();
  const m = ctx.matrix;
  const roles = ROLE_ORDER.filter((r) => ctx.roles.includes(r)).map(String);

  if (looksSensitive(text)) {
    return {
      decision: "REFUSE",
      intent: "SENSITIVE_INPUT",
      sensitive: true,
      text: "That looks like it may contain a password, code, key or card number, so I did not read it. Please don't type secrets here. Ask in general terms, for example: can I invite a viewer?",
      links: [],
      actions: [],
    };
  }
  if (!text) return { decision: "ABSTAIN", intent: "EMPTY", text: "Ask me what your role can do, who can do something, or where to find a page.", links: [], actions: [] };

  if (RX.greeting.test(text)) {
    return ans(
      "HELP",
      `I'm the console guide. I explain what your role (${roles.length ? roles.map(ROLE_NAME).join(" and ") : "member"}) can do, who can do what, and where things are. I can't look up your company's data or change anything.`,
    );
  }

  const q = tokens(text);
  const mentioned = mentionedRoles(text);

  if (/\b(delete|close|dissolve|terminate|shut ?down|cancel)\b.{0,24}\b(company|organi[sz]ation|tenant|workspace)\b/i.test(text)) {
    return {
      decision: "ABSTAIN",
      intent: "NOT_IN_MATRIX",
      text: "Closing or deleting the company is not an action in the role matrix, so I can't say any role may do it. Please ask your company Owner or Zoiko iD support.",
      links: [],
      actions: [],
    };
  }

  // 1) Requests to DO something or to read company data: declined, with the page and what the person's role allows.
  const wantsAction = RX.action.test(text) || /\b(for me|on my behalf)\b/i.test(text);
  const wantsData = RX.data.test(text);
  if (wantsAction || wantsData) {
    const found = findActions(m, q);
    const page = findPage(q, true);
    const links = (found[0] ? pagesFor(found[0]) : page ? [page] : []).map(link);
    const base = wantsData
      ? "I can't look up your company's data; I can only explain roles and where things are."
      : "I can't do that for you; this guide only explains, it never changes anything.";
    const first = found[0];
    let more = "";
    if (first) {
      const mode = best(modesFor(first, roles));
      more = ` About "${titleCase(first)}": your role ${roleCan(mode)}.`;
    }
    const where = links[0] ? ` You can do it on the ${links[0].label} page, if your role allows.` : "";
    return { decision: "REFUSE", intent: wantsData ? "DATA_LOOKUP" : "ACTION_REQUEST", text: `${base}${more}${where}`, links, actions: first ? [first.action] : [] };
  }

  // 2) Summary of the caller's own permissions.
  if (RX.summary.test(text) && mentioned.length === 0) {
    if (roles.length === 0) {
      return ans(
        "ROLE_SUMMARY",
        "You are signed in without a company role, so you can use Overview, Alerts and your own account (sessions, passkeys, recovery codes and privacy). Ask an Owner if you need more. " + NOTE,
        { links: ["overview", "account", "passkeys"].map((id) => link(PAGES.find((p) => p.id === id) as GuidePage)) },
      );
    }
    const lines = roles.flatMap((r) => {
      const run = examples(m, r, "X");
      const req = examples(m, r, "S");
      const app = examples(m, r, "A");
      const parts = [describeCounts(m, r)];
      if (run.length) parts.push(`You can run, for example: ${run.join("; ")}.`);
      if (req.length) parts.push(`You can prepare or request, for example: ${req.join("; ")}.`);
      if (app.length) parts.push(`You can approve, for example: ${app.join("; ")}.`);
      return parts;
    });
    return ans("ROLE_SUMMARY", `You hold: ${roles.map(ROLE_NAME).join(" and ")}. ${lines.join(" ")} ${NOTE}`, { links: ["team", "approvals"].filter(() => roles.some((r) => r !== "VIEWER")).map((id) => link(PAGES.find((p) => p.id === id) as GuidePage)) });
  }

  // 3) Becoming something with more access.
  if (RX.elevate.test(text)) {
    const reqRow = m.matrices.flatMap((g) => g.actions).find((a) => a.action === "a.request-admin-approver-role-grant");
    const apprRow = m.matrices.flatMap((g) => g.actions).find((a) => a.action === "a.approve-privileged-role-grant");
    const requesters = reqRow ? ROLE_ORDER.filter((r) => reqRow.permissions[r] === "S") : [];
    const approvers = apprRow ? ROLE_ORDER.filter((r) => apprRow.permissions[r] === "A") : [];
    const how =
      `Nobody can give themselves a higher role. Admin and Approver roles are granted through a request: an ${requesters.length ? joinOr(requesters) : "Owner or Admin"} starts it, and an independent ${approvers.length ? joinOr(approvers) : "Owner or Approver"} approves it. ` +
      "Owner access changes only through the protected ownership transfer, which also needs an independent approval. Ask an Owner to start the request for you.";
    const mine = reqRow ? best(modesFor(reqRow, roles)) : "-";
    const own = mine === "-" ? " Requesting a role grant is not part of your role." : ` For requesting a role grant, you can ${MODE_PHRASE[mine]}.`;
    return ans("ELEVATION", `${how}${own} ${NOTE}`, {
      links: ["team", "approvals"].map((id) => link(PAGES.find((p) => p.id === id) as GuidePage)),
      actions: [reqRow?.action, apprRow?.action].filter((x): x is string => Boolean(x)),
    });
  }

  // 4) Comparing or describing roles.
  if (mentioned.length >= 2 && RX.compare.test(text)) {
    const [a, b] = mentioned as [string, string];
    const diffs = m.matrices.flatMap((g) => g.actions).filter((x) => (x.permissions[a] ?? "-") !== (x.permissions[b] ?? "-"));
    const lines = diffs.slice(0, 5).map((x) => `${titleCase(x)}: ${ROLE_NAME(a)} ${x.permissions[a] ?? "-"}, ${ROLE_NAME(b)} ${x.permissions[b] ?? "-"}`);
    return ans(
      "ROLE_COMPARE",
      `${ROLE_TEXT[a] ?? ""} ${ROLE_TEXT[b] ?? ""} They differ on ${diffs.length} of ${m.matrices.reduce((n, g) => n + g.actions.length, 0)} matrix items, for example ${lines.join("; ")}. (X run, S request, A approve, R view, T own account, - none.)`,
    );
  }
  if (mentioned.length >= 1 && (RX.describeRole.test(text) || (/\b(role|do|does|can)\b/i.test(text) && findActions(m, q).length === 0))) {
    const r = mentioned[0] as string;
    return ans("ROLE_DESCRIBE", `${ROLE_TEXT[r] ?? ""} ${describeCounts(m, r)}`);
  }

  // 5) Who can do something.
  let found = findActions(m, q);
  if (found[0]?.action === "a.invite-operator-or-viewer" && mentioned.some((r) => r === "ADMIN" || r === "APPROVER" || r === "OWNER")) {
    // Inviting someone as Admin, Approver or Owner is not the ordinary invitation: it is a role-grant request that an independent person approves.
    const reqRow = m.matrices.flatMap((g) => g.actions).find((a) => a.action === "a.request-admin-approver-role-grant");
    if (reqRow) found = [reqRow, ...found.filter((a) => a.action !== reqRow.action)];
  }
  if (RX.who.test(text)) {
    const first = found[0];
    if (first) {
      return ans("WHO_CAN", `${titleCase(first)}: ${who(first)} ${first.rule}`, { actions: [first.action], links: pagesFor(first).map(link) });
    }
  }

  // 6) Can I ... / why can't I ...
  if (RX.canI.test(text) || (mentioned.length > 0 && found.length > 0)) {
    const first = found[0];
    if (first) {
      const modes = modesFor(first, roles);
      const top = best(modes);
      const per = modes.length > 1 ? ` (${modes.map((x) => `${ROLE_NAME(x.role)}: ${MODE_LABEL[x.mode]}`).join("; ")})` : "";
      const missing = top === "-" ? ` ${who(first)}${rolesByMode(first).size ? " To get access, ask an Owner; roles are granted through a request that an independent Owner or Approver approves." : ""}` : "";
      const head = top === "-" ? `No. "${titleCase(first)}" is not part of your role${per}.` : `"${titleCase(first)}": you can ${MODE_PHRASE[top]}${per}.`;
      const rule = first.rule ? ` Rule: ${first.rule}` : "";
      const others = found.slice(1, 3).map(titleCase);
      const related = others.length ? ` Related: ${others.join("; ")}.` : "";
      return ans("CAN_I", `${head}${missing}${rule}${related} ${NOTE}`, { actions: found.map((a) => a.action), links: pagesFor(first).map(link) });
    }
  }

  // 7) Where / how do I ...
  const page = findPage(q);
  if (RX.where.test(text) || (page && q.length <= 3)) {
    if (page) {
      const act = found[0];
      const perm = act ? ` For "${titleCase(act)}", your role ${roleCan(best(modesFor(act, roles)))}.` : "";
      return ans("WHERE", `You'll find that under ${page.label} in the ${page.group} menu. If you don't see it in your menu, your role may not include it.${perm}`, { links: [link(page)], actions: act ? [act.action] : [] });
    }
  }

  // 8) A strong action match without a recognised phrasing: explain what the role allows.
  if (found[0]) {
    const first = found[0];
    const top = best(modesFor(first, roles));
    return ans("CAN_I", `"${titleCase(first)}": your role ${roleCan(top)}. ${who(first)} ${NOTE}`, { actions: [first.action], links: pagesFor(first).map(link) });
  }
  if (page) return ans("WHERE", `That is under ${page.label} in the ${page.group} menu.`, { links: [link(page)] });

  return {
    decision: "ABSTAIN",
    intent: "UNKNOWN",
    text: "I can answer questions about what your role can do, who can do something, what the roles mean, and where a page is. Try: What can I do with my role? / Can I invite a viewer? / Who can approve role grants?",
    links: [],
    actions: [],
  };
}
