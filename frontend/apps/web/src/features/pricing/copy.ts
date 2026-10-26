/** The only marketing claims on the pricing page (ZID-COM-001 §19) and the meter glossary (§7). A test scans
 * every string here for certification, compliance and availability wording. */
export const HERO = "Identity infrastructure that scales with your trust requirements.";
export const SUBHERO = "Start building free. Add verification, federation and governance when your use case demands it.";

export const PLAN_ORDER = ["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"] as const;

export const SKU_LABEL: Record<string, string> = {
  "ZID-BASE": "Platform fee (per month)",
  "ZID-HUM": "Active person above the included amount",
  "ZID-AUTH": "Sign-ins above the included amount",
  "ZID-PDP": "Access decisions above the included amount",
  "ZID-M2M": "Machine grants above the included amount",
  "ZID-FED": "Extra SSO connection (per month)",
  "ZID-SCIM": "Extra directory connection (per month)",
  "ZID-VCI": "Credential issued",
  "ZID-VCP": "Credential presentation checked",
};

export const GLOSSARY: { term: string; meaning: string }[] = [
  {
    term: "Monthly active person",
    meaning:
      "One distinct person with at least one completed production sign-in or qualifying action in a calendar month (UTC), counted once inside your organization even if their identities are linked or merged.",
  },
  {
    term: "Sign-in",
    meaning: "One successful sign-in. Redirects, extra factors, token refreshes, failed attempts and retries are not counted.",
  },
  {
    term: "Access decision",
    meaning: "One final answer from the policy engine, whether it allows or denies. Retries and cached answers are not counted.",
  },
  {
    term: "Machine grant",
    meaning: "One scoped credential issued to a service or agent in production.",
  },
  {
    term: "Credential issued / presentation checked",
    meaning: "One credential successfully issued, or one presentation cryptographically checked.",
  },
];

export const ADD_ONS =
  "Identity and business verification, screening, messaging, extended archives, dedicated regions and professional services are priced separately, depend on country and supplier, and are quoted rather than listed here.";

export const DISCLAIMER =
  "Prices are in the currency shown, exclude taxes and third-party checks, and may change for new orders. The calculator gives an estimate, not a quotation; your order form is the only binding price.";

export const ENTERPRISE_NOTE = "Enterprise terms, volumes and the platform fee are negotiated; the fee shown is a starting point.";
