/** Plain-language meaning of OAuth scopes the cell issues. Unknown scopes are shown verbatim. */
const SCOPES: Record<string, string> = {
  openid: "Confirm who you are (your Zoiko iD identifier)",
  profile: "See your basic profile",
  email: "See your email address",
  offline_access: "Stay connected when you are not using it (refresh tokens)",
  "verification:read": "See your identity verification results and assurance level",
  "verification:execute": "Start identity verification checks for you",
  "presentation:request": "Ask you to present a digital credential",
};

export function scopeDescription(scope: string): string {
  return SCOPES[scope] ?? scope;
}

export function isKnownScope(scope: string): boolean {
  return scope in SCOPES;
}
